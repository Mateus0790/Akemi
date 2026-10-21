import asyncio
import threading
import time
from dataclasses import dataclass

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
