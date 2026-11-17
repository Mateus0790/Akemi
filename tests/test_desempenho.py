"""Desempenho: a sua voz vai na frente do áudio do PC, o áudio do PC tem limite
de CPU e cada resposta à voz loga onde o tempo foi parar."""

import asyncio
import threading
import time

import numpy as np
import pytest
from structlog.testing import capture_logs

from akemi.core.events import Event, EventBus, Priority, Trust
from akemi.core.pause import PauseSwitch
from akemi.stt.listener import AmbientAudioInput, OwnerVoiceInput
from akemi.stt.transcriber import SharedTranscriber, Transcript

LOUD = (np.ones(320, dtype=np.int16) * 3000).tobytes()
SILENCE = b"\x00" * 640
UTTERANCE = [LOUD] * 20 + [SILENCE] * 40  # uma frase e o silêncio que a encerra


class FakeSpeech:
    speaking = False
    recent_speech: list = []


class BlockingWhisper:
    """Whisper falso que só termina quando o teste manda."""

    def __init__(self):
        self.calls: list[bytes] = []
        self.release = threading.Event()

    def __call__(self, pcm16: bytes) -> Transcript:
        self.calls.append(pcm16)
        assert self.release.wait(5)
        return Transcript(f"texto {len(self.calls)}", avg_logprob=-0.1)


async def until(cond, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "demorou demais"
        await asyncio.sleep(0.005)


# ------------------------------------------------------- Whisper compartilhado
@pytest.mark.asyncio
async def test_pc_audio_is_dropped_while_your_voice_is_transcribed():
    whisper = BlockingWhisper()
    shared = SharedTranscriber(whisper)
    mine = asyncio.create_task(shared.transcribe(b"mic"))
    await until(lambda: whisper.calls)
    assert shared.busy
    assert await shared.transcribe_background(b"pc") is None  # não faz fila
    whisper.release.set()
    result = await mine
    assert result == "texto 1" and result.avg_logprob == -0.1
    assert whisper.calls == [b"mic"]
    report = shared.load_report()
    assert report["mic_segments"] == 1 and report["pc_segments"] == 0 and report["pc_skipped"] == 1


@pytest.mark.asyncio
async def test_your_voice_waits_only_for_the_pc_segment_already_running():
    whisper = BlockingWhisper()
    shared = SharedTranscriber(whisper)
    pc = asyncio.create_task(shared.transcribe_background(b"pc1"))
    await until(lambda: whisper.calls)
    mine = asyncio.create_task(shared.transcribe(b"mic"))
    await asyncio.sleep(0.05)
    # Com você esperando, mais áudio do PC não entra na sua frente.
    assert await shared.transcribe_background(b"pc2") is None
    whisper.release.set()
    await pc
    result = await mine
    assert whisper.calls == [b"pc1", b"mic"]
    assert result.queued_s >= 0.04  # esperou o trecho do PC que já estava rodando
    assert result.took_s >= 0.0
    assert not shared.busy


@pytest.mark.asyncio
async def test_load_report_is_logged_every_window():
    now = {"t": 0.0}

    def whisper(pcm16):
        now["t"] += 30.0 if pcm16 == b"a" else 31.0  # relógio falso: o Whisper "leva" 30 s / 31 s
        return Transcript("oi")

    shared = SharedTranscriber(whisper, clock=lambda: now["t"], report_every_s=60)
    with capture_logs() as logs:
        await shared.transcribe(b"a")
        await shared.transcribe_background(b"b")  # 61 s: fecha a janela de 60 s
    load = [entry for entry in logs if entry["event"] == "whisper load"]
    assert len(load) == 1
    assert load[0]["busy"] == "100%"
    assert load[0]["mic"] == "1 trechos, 30.0s" and load[0]["pc"] == "1 trechos, 31.0s"
    assert shared.load_report()["mic_segments"] == 0  # janela nova


# ------------------------------------------------------------- áudio do PC
def make_ambient(transcribe, **kw):
    bus = EventBus()
    events = []
    bus.publish = events.append
    amb = AmbientAudioInput(bus, transcribe, lambda f: f != SILENCE, FakeSpeech(), PauseSwitch(), **kw)
    return amb, events


async def feed_all(inp, frames):
    for frame in frames:
        await inp.feed(frame)


@pytest.mark.asyncio
async def test_pc_audio_yields_while_you_are_talking():
    calls = []

    async def transcribe(seg):
        calls.append(seg)
        return "notícia interessante"

    talking = {"now": True}
    amb, events = make_ambient(transcribe, owner_talking=lambda: talking["now"])
    await feed_all(amb, UTTERANCE)
    assert calls == [] and amb.dropped == {"owner_talking": 1}
    talking["now"] = False
    await feed_all(amb, UTTERANCE)
    assert len(calls) == 1 and [e.kind for e in events] == ["ambient.audio"]


@pytest.mark.asyncio
async def test_pc_audio_dropped_when_whisper_busy():
    async def busy(seg):
        return None

    amb, events = make_ambient(busy)
    await feed_all(amb, UTTERANCE)
    assert events == [] and amb.dropped == {"whisper_busy": 1}


@pytest.mark.asyncio
async def test_pc_audio_cpu_budget():
    """25%: cada segundo de Whisper com o áudio do PC custa 3 s de descanso."""
    now = {"t": 0.0}

    async def transcribe(seg):
        t = Transcript("notícia interessante")
        t.took_s = 4.0  # PC lento: 4 s para transcrever
        return t

    amb, events = make_ambient(transcribe, max_load=0.25, burst_s=4.0, clock=lambda: now["t"])
    await feed_all(amb, UTTERANCE)  # gasta o balde inteiro
    await feed_all(amb, UTTERANCE)
    assert len(events) == 1 and amb.dropped == {"cpu_budget": 1}
    now["t"] += 1.0  # 1 s depois: 0,25 s no balde, dá para mais um
    await feed_all(amb, UTTERANCE)
    assert len(events) == 2


@pytest.mark.asyncio
async def test_pc_audio_without_limit():
    async def transcribe(seg):
        t = Transcript("notícia interessante")
        t.took_s = 100.0
        return t

    amb, events = make_ambient(transcribe, max_load=1.0)
    for _ in range(3):
        await feed_all(amb, UTTERANCE)
    assert len(events) == 3 and amb.dropped == {}


# ------------------------------------------------------------- cronômetro
@pytest.mark.asyncio
async def test_owner_message_carries_latency():
    bus = EventBus()
    events = []
    bus.publish = events.append

    async def transcribe(seg):
        await asyncio.sleep(0.02)
        t = Transcript("Akemi, que horas são?")
        t.queued_s = 0.5
        return t

    vi = OwnerVoiceInput(bus, transcribe, lambda f: f != SILENCE, FakeSpeech(), PauseSwitch(), end_silence_ms=600)
    await feed_all(vi, UTTERANCE)
    [msg] = [e for e in events if e.kind == "owner.message"]
    lat = msg.payload["latency"]
    assert lat["end_silence_ms"] == 600
    assert lat["stt_queue_ms"] == 500
    assert lat["voice_id_ms"] == 0
    assert 0 <= lat["stt_ms"] < 500  # o tempo de fila não conta duas vezes
    assert lat["heard_at"] <= time.monotonic()


@pytest.mark.asyncio
async def test_reply_latency_logged_once_per_answer(tmp_path):
    from tests.test_mind_integration import make_akemi

    akemi, tts, _ = make_akemi(lambda msgs, json_mode: "São três horas. Quer que eu te lembre de algo?", tmp_path)
    await akemi.start()
    heard = time.monotonic() - 0.4
    latency = {"heard_at": heard, "end_silence_ms": 700.0, "stt_queue_ms": 100, "stt_ms": 200, "voice_id_ms": 50}
    with capture_logs() as logs:
        akemi.bus.publish(
            Event(
                "owner.message",
                {"text": "que horas são?", "via": "mic", "latency": latency},
                source="mic",
                trust=Trust.OWNER,
                priority=Priority.HIGH,
            )
        )
        await akemi.bus.drain()
        await asyncio.sleep(0.3)
    await akemi.stop()
    assert tts.said == ["São três horas.", "Quer que eu te lembre de algo?"]
    lines = [entry for entry in logs if entry["event"] == "voice reply latency"]
    assert len(lines) == 1  # uma linha por resposta, não por frase
    line = lines[0]
    assert line["end_silence_ms"] == 700 and line["stt_queue_ms"] == 100 and line["voice_id_ms"] == 50
    assert line["total_ms"] >= 700 + 400
    assert line["brain_ms"] >= 0
    assert line["total_ms"] == pytest.approx(
        line["end_silence_ms"] + line["stt_queue_ms"] + line["stt_ms"] + line["voice_id_ms"] + line["brain_ms"],
        abs=2,
    )


@pytest.mark.asyncio
async def test_typed_messages_have_no_latency_line(tmp_path):
    from tests.test_mind_integration import make_akemi

    akemi, tts, _ = make_akemi(lambda msgs, json_mode: "Oi!", tmp_path)
    await akemi.start()
    with capture_logs() as logs:
        akemi.owner_say("oi")
        await akemi.bus.drain()
        await asyncio.sleep(0.2)
    await akemi.stop()
    assert tts.said == ["Oi!"]
    assert not [entry for entry in logs if entry["event"] == "voice reply latency"]


# ------------------------------------------------- WhisperTranscriber (falso)
@pytest.mark.asyncio
async def test_whisper_transcriber_settings_and_shared_path(monkeypatch):
    import sys
    import types
    from types import SimpleNamespace

    from akemi.stt.transcriber import WhisperTranscriber

    made = {}

    class FakeModel:
        def __init__(self, size, **kw):
            made.update(size=size, **kw)

        def transcribe(self, audio, **kw):
            seg = SimpleNamespace(text=" oi Akemi ", start=0.0, end=1.0, avg_logprob=-0.2, no_speech_prob=0.1)
            return iter([seg]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    stt = WhisperTranscriber("base", "pt", device="cuda", cpu_threads=2)
    assert made == {"size": "base", "device": "cuda", "compute_type": "int8_float16", "cpu_threads": 2}
    result = await stt.transcribe(b"\x00\x00" * 1600)
    assert result == "oi Akemi" and result.no_speech_prob == 0.1 and result.took_s >= 0
    assert (await stt.transcribe_background(b"\x00\x00" * 1600)) == "oi Akemi"
    assert not stt.busy

    WhisperTranscriber("small", "pt")
    assert made["compute_type"] == "int8" and made["cpu_threads"] == 0 and made["device"] == "auto"
