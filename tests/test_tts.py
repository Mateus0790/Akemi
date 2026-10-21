import asyncio
import threading
import time
from dataclasses import dataclass

import numpy as np
import pytest

from akemi.core.config import Settings
from akemi.core.events import Event, EventBus
from akemi.core.orchestrator import Akemi, build_tts
from akemi.safety.moderation import Moderator
from akemi.tts.base import ConsoleTTS, Speaker, TTSEngine
from akemi.tts.piper_engine import PiperTTS


@dataclass
class Chunk:
    audio_int16_bytes: bytes
    sample_rate: int = 22050
    sample_channels: int = 1


class FakeVoice:
    def __init__(self, n_chunks=3, delay=0.0):
        self.n_chunks, self.delay, self.calls = n_chunks, delay, []

    def synthesize(self, text, syn_config=None):
        self.calls.append((text, syn_config))
        for i in range(self.n_chunks):
            time.sleep(self.delay)
            yield Chunk(bytes([i]) * 100)


class FakePlayer:
    def __init__(self, delay=0.0):
        self.played, self.delay = [], delay

    def play(self, pcm16, sample_rate, channels, cancel: threading.Event):
        for _ in range(10):
            if cancel.is_set():
                return
            time.sleep(self.delay / 10)
        self.played.append((pcm16, sample_rate, channels))


@pytest.mark.asyncio
async def test_piper_plays_all_chunks():
    voice, player = FakeVoice(), FakePlayer()
    tts = PiperTTS("nao/existe.onnx", voice=voice, player=player, length_scale=1.2)
    await tts.speak("Olá, Mateus!")
    assert [p[0][0] for p in player.played] == [0, 1, 2]
    assert player.played[0][1] == 22050
    assert voice.calls[0][0] == "Olá, Mateus!"


@pytest.mark.asyncio
async def test_piper_stops_quickly_when_cancelled():
    voice, player = FakeVoice(n_chunks=50), FakePlayer(delay=0.05)
    tts = PiperTTS("x.onnx", voice=voice, player=player)
    task = asyncio.create_task(tts.speak("uma fala bem longa"))
    await asyncio.sleep(0.12)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    assert 0 < len(player.played) < 10
    # Depois de interrompida, a próxima fala funciona normalmente
    player2 = FakePlayer()
    tts._player = player2
    await tts.speak("de novo")
    assert len(player2.played) == 50


def test_piper_missing_model_has_helpful_error(tmp_path):
    tts = PiperTTS(tmp_path / "pt_BR-faber-medium.onnx", player=FakePlayer())
    with pytest.raises(FileNotFoundError, match="download_voices pt_BR-faber-medium"):
        tts.load()


def test_build_tts_selects_engine(tmp_path):
    assert isinstance(build_tts(Settings(_env_file=None)), ConsoleTTS)
    s = Settings(_env_file=None, tts_engine="piper", piper_model_path=tmp_path / "v.onnx")
    engine = build_tts(s)
    assert isinstance(engine, (PiperTTS, ConsoleTTS))  # Console se sounddevice faltar


@pytest.mark.asyncio
async def test_akemi_falls_back_to_console_if_voice_missing(tmp_path):
    s = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0)
    akemi = Akemi(s, tts=PiperTTS(tmp_path / "nada.onnx", player=FakePlayer()), persist=False)
    await akemi.start()
    assert isinstance(akemi.speaker.engine, ConsoleTTS)
    await akemi.stop()


class SlowTTS(TTSEngine):
    def __init__(self):
        self.done = []

    async def speak(self, text):
        await asyncio.sleep(0.2)
        self.done.append(text)


@pytest.mark.asyncio
async def test_speaker_events_and_interrupt():
    bus = EventBus()
    seen = []
    bus.subscribe("speech.*", lambda e: seen.append((e.kind, e.payload.get("status"))))
    engine = SlowTTS()
    sp = Speaker(bus, engine, Moderator())
    sp.register()
    bus.start()
    sp.start()
    await sp.on_say(Event("speech.say", {"text": "primeira"}))
    await sp.on_say(Event("speech.say", {"text": "segunda"}))
    await asyncio.sleep(0.05)
    assert sp.speaking
    await sp.on_stop()
    await asyncio.sleep(0.1)
    await bus.drain()
    await sp.stop()
    await bus.stop()
    assert engine.done == []  # interrompeu a 1ª e descartou a 2ª
    assert ("speech.started", None) in seen and ("speech.finished", "interrupted") in seen
    assert not sp.speaking


# ------------------------------------------------------------------ Kokoro
class FakeKokoro:
    def __init__(self, delay=0.0):
        self.delay, self.calls = delay, []

    def get_voices(self):
        return ["pf_dora", "pm_alex"]

    def create(self, text, voice, speed, lang):
        self.calls.append((text, voice, speed, lang))
        time.sleep(self.delay)
        return np.array([0.0, 0.5, -0.5, 2.0], dtype=np.float32), 24000


@pytest.mark.asyncio
async def test_kokoro_speaks_pcm16_with_dora():
    from akemi.tts.kokoro_engine import KokoroTTS

    kokoro, player = FakeKokoro(), FakePlayer()
    tts = KokoroTTS("m.onnx", "v.bin", kokoro=kokoro, player=player, speed=1.1)
    await tts.speak("Oi, Mateus!")
    assert kokoro.calls == [("Oi, Mateus!", "pf_dora", 1.1, "pt-br")]
    pcm, sr, ch = player.played[0]
    assert (sr, ch) == (24000, 1)
    assert np.frombuffer(pcm, dtype=np.int16).tolist() == [0, 16383, -16383, 32767]  # 2.0 é cortado


@pytest.mark.asyncio
async def test_kokoro_interrupted_while_synthesizing_does_not_play():
    from akemi.tts.kokoro_engine import KokoroTTS

    player = FakePlayer()
    tts = KokoroTTS("m.onnx", "v.bin", kokoro=FakeKokoro(delay=0.3), player=player)
    task = asyncio.create_task(tts.speak("uma frase"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.4)  # a thread termina a síntese e não toca nada
    assert player.played == []


def test_kokoro_missing_files_raise_clear_error(tmp_path):
    from akemi.tts.kokoro_engine import KokoroTTS

    with pytest.raises(FileNotFoundError, match="Kokoro"):
        KokoroTTS(tmp_path / "x.onnx", tmp_path / "v.bin").load()


def test_build_tts_kokoro(tmp_path):
    from akemi.tts.kokoro_engine import KokoroTTS

    s = Settings(_env_file=None, tts_engine="kokoro", kokoro_voice="pm_alex")
    engine = build_tts(s)
    assert isinstance(engine, KokoroTTS) and engine.voice == "pm_alex"


def test_player_blocks_are_whole_samples(monkeypatch):
    """22050 Hz × 50 ms = 2205 bytes: o bloco precisa ser par (int16)."""
    import sys
    import types

    writes = []

    class Stream:
        def __init__(self, **kw):
            pass

        def start(self):
            pass

        def write(self, data):
            assert len(data) % 2 == 0, "bloco cortou uma amostra no meio"
            writes.append(len(data))

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawOutputStream=Stream))
    from akemi.tts.piper_engine import SoundDevicePlayer

    SoundDevicePlayer().play(b"\x00\x01" * 22050, 22050, 1, threading.Event())
    assert sum(writes) == 44100 and writes[0] == 2204
