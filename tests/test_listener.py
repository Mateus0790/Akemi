import time

import numpy as np
import pytest

from akemi.core.events import EventBus, Trust
from akemi.core.pause import PauseSwitch
from akemi.stt.filters import contains_wake_word, is_echo, is_hallucination
from akemi.stt.listener import AmbientAudioInput, OwnerVoiceInput, rms

LOUD = (np.ones(320, dtype=np.int16) * 3000).tobytes()
SOFT = (np.ones(320, dtype=np.int16) * 300).tobytes()
SILENCE = b"\x00" * 640


class FakeSpeech:
    def __init__(self):
        self.speaking = False
        self.recent_speech = []


def make(texts, mode="wake_word", speech=None):
    bus = EventBus()
    events = []
    bus.publish = events.append  # captura síncrona
    it = iter(texts)

    async def transcribe(seg):
        return next(it)

    speech = speech or FakeSpeech()
    vi = OwnerVoiceInput(bus, transcribe, lambda f: f != SILENCE, speech, PauseSwitch(), mode=mode)
    return vi, events, speech


def kinds(events):
    return [e.kind for e in events]


# ------------------------------------------------------------------ filtros
@pytest.mark.parametrize("text", ["Akemi, que horas são?", "aquemi tá aí?", "ei a kemi", "AKIMI liga a luz"])
def test_wake_word_tolerates_whisper_spelling(text):
    assert contains_wake_word(text, ["akemi"])


@pytest.mark.parametrize("text", ["que horas são?", "a camisa é minha", "academia amanhã"])
def test_wake_word_no_false_positive(text):
    assert not contains_wake_word(text, ["akemi"])


def test_hallucinations():
    assert is_hallucination("Obrigado por assistir!")
    assert is_hallucination("Legendas pela comunidade Amara.org")
    assert is_hallucination("ok ok ok ok ok")
    assert is_hallucination("")
    assert not is_hallucination("obrigado pela ajuda com o código")


def test_echo_detection_respects_window():
    recent = [(100.0, "Eita, deu erro aí. Quer ajuda?")]
    assert is_echo("deu erro aí quer ajuda", recent, now=102.0)
    assert not is_echo("deu erro aí quer ajuda", recent, now=200.0)
    assert not is_echo("abre o navegador", recent, now=102.0)


def test_rms():
    assert rms(SILENCE) == 0 and rms(LOUD) == pytest.approx(3000)


# ----------------------------------------------------------------- listener
@pytest.mark.asyncio
async def test_wake_word_mode_publishes_owner_message():
    vi, events, _ = make(["Akemi, abre o bloco de notas"])
    out = await vi.handle_segment(b"x", now=1000.0)
    assert out == "Akemi, abre o bloco de notas"
    msg = events[-1]
    assert msg.kind == "owner.message" and msg.trust == Trust.OWNER and msg.payload["via"] == "mic"


@pytest.mark.asyncio
async def test_not_addressed_is_dropped_but_followup_works():
    vi, events, _ = make(["você viu o jogo ontem?", "e o tempo amanhã?", "e agora?"])
    assert await vi.handle_segment(b"x", now=1000.0) is None
    assert vi.dropped["not_addressed"] == 1 and events == []
    vi.mark_exchange(1000.0)  # ela acabou de responder
    assert await vi.handle_segment(b"x", now=1010.0) == "e o tempo amanhã?"
    # a janela de continuação renova a cada fala
    assert await vi.handle_segment(b"x", now=1025.0) == "e agora?"


@pytest.mark.asyncio
async def test_always_mode_accepts_everything():
    vi, events, _ = make(["que horas são?"], mode="always")
    assert await vi.handle_segment(b"x") == "que horas são?"


@pytest.mark.asyncio
async def test_drops_echo_and_hallucination():
    speech = FakeSpeech()
    speech.recent_speech = [(time.time(), "Akemi aqui, tudo certo por aí?")]
    vi, events, _ = make(["Akemi aqui, tudo certo por aí?", "Obrigado por assistir."], speech=speech)
    assert await vi.handle_segment(b"x") is None
    assert await vi.handle_segment(b"x") is None
    assert vi.dropped == {"echo": 1, "hallucination": 1} and events == []


@pytest.mark.asyncio
async def test_feed_full_flow_and_barge_in():
    vi, events, speech = make(["Akemi, para"], mode="wake_word")
    speech.speaking = True
    # Voz baixa enquanto ela fala (vazamento da caixa de som) não interrompe
    for _ in range(20):
        await vi.feed(SOFT)
    assert events == []
    # Voz alta perto do mic interrompe imediatamente, antes de transcrever
    for _ in range(6):
        await vi.feed(LOUD)
    assert kinds(events) == ["owner.speech_started"]
    for _ in range(10):
        await vi.feed(LOUD)
    speech.speaking = False
    out = None
    for _ in range(40):
        out = out or await vi.feed(SILENCE)
    assert out == "Akemi, para"
    assert kinds(events)[-1] == "owner.message"


@pytest.mark.asyncio
async def test_ambient_skips_while_speaking_or_paused():
    bus = EventBus()
    events = []
    bus.publish = events.append
    speech, pause = FakeSpeech(), PauseSwitch()

    async def transcribe(seg):
        return "notícia interessante no vídeo"

    amb = AmbientAudioInput(bus, transcribe, lambda f: f != SILENCE, speech, pause)
    speech.speaking = True
    for f in [LOUD] * 20 + [SILENCE] * 40:
        await amb.feed(f)
    assert events == []
    speech.speaking = False
    for f in [LOUD] * 20 + [SILENCE] * 40:
        await amb.feed(f)
    assert kinds(events) == ["ambient.audio"] and events[0].trust == Trust.PUBLIC


# ------------------------------------------- chamado sozinho e confiança
@pytest.mark.parametrize("text", ["Akemi", "Akemi.", "Alô, Akemi!", "ei a kemi", "Oi Aquemi?"])
def test_bare_wake_word(text):
    from akemi.stt.filters import is_bare_wake_word

    assert is_bare_wake_word(text, ["akemi"])


@pytest.mark.parametrize("text", ["Akemi, que horas são?", "Akemi abre o Discord", "que horas são"])
def test_not_bare_wake_word(text):
    from akemi.stt.filters import is_bare_wake_word

    assert not is_bare_wake_word(text, ["akemi"])


@pytest.mark.asyncio
async def test_bare_wake_word_waits_for_the_request():
    vi, events, _ = make(["Akemi.", "que horas são?"])
    assert await vi.handle_segment(b"x", now=1000.0) is None
    assert kinds(events) == ["owner.speech_started", "owner.wake"]  # parou de falar, escutando
    assert await vi.handle_segment(b"x", now=1004.0) == "que horas são?"
    assert kinds(events)[-1] == "owner.message" and events[-1].text == "que horas são?"


@pytest.mark.asyncio
async def test_low_confidence_needs_her_name():
    from akemi.stt.transcriber import Transcript

    noise = Transcript("de sésar.", avg_logprob=-1.3, no_speech_prob=0.2)
    silence = Transcript("Seza. Tá bom.", avg_logprob=-0.3, no_speech_prob=0.9)
    called = Transcript("Akemi, abre o Discord", avg_logprob=-1.0, no_speech_prob=0.1)
    vi, events, _ = make([noise, silence, called])
    vi.mark_exchange(1000.0)  # dentro da janela de continuação
    assert await vi.handle_segment(b"x", now=1002.0) is None
    assert await vi.handle_segment(b"x", now=1003.0) is None
    assert vi.dropped == {"low_confidence": 1, "no_speech": 1}
    # Com o nome dela, aceita mesmo com confiança baixa.
    assert await vi.handle_segment(b"x", now=1004.0) == "Akemi, abre o Discord"


@pytest.mark.asyncio
async def test_noise_heard_as_her_name_is_dropped():
    """Medido no PC do dono: chiado vira "Akemi" com no_speech_prob 0,88."""
    from akemi.stt.transcriber import Transcript

    vi, events, speech = make([Transcript("Akemi", avg_logprob=-0.84, no_speech_prob=0.88)])
    speech.speaking = True
    assert await vi.handle_segment(b"x", now=1000.0) is None
    assert events == []  # não interrompe a fala dela


def test_transcript_is_a_str_with_confidence():
    from akemi.stt.transcriber import Transcript

    t = Transcript(" oi ", -0.4, 0.1)
    assert t == " oi " and t.strip() == "oi" and t.avg_logprob == -0.4 and t.no_speech_prob == 0.1
