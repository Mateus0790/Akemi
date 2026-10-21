"""Visão: detecção de mudança, limpeza do OCR e o ScreenWatcher (sem tela real)."""

import asyncio
import json

import httpx
import numpy as np
import pytest

from akemi.brain.providers import AnthropicProvider, EchoProvider, Message, OpenAICompatibleProvider
from akemi.core.config import Settings
from akemi.core.events import Trust
from akemi.core.orchestrator import Akemi
from akemi.core.pause import PauseSwitch
from akemi.tts.base import TTSEngine
from akemi.vision.frame_diff import ChangeDetector, changed_fraction, thumbnail
from akemi.vision.ocr import clean_ocr_text, similarity
from akemi.vision.watcher import ScreenWatcher


def frame(value: int, size: int = 80) -> np.ndarray:
    return np.full((size, size, 3), value, dtype=np.uint8)


class FakeBus:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class FakeScreen:
    """Tela controlada pelo teste: frame, título e texto que o "OCR" devolve."""

    def __init__(self, img=None, title="", text=""):
        self.img = frame(0) if img is None else img
        self.title = title
        self.text = text
        self.grabs = 0
        self.reads = 0

    def grab(self):
        self.grabs += 1
        return self.img

    async def read(self, _img):
        self.reads += 1
        return self.text


def watcher(screen, bus=None, **kw):
    return ScreenWatcher(bus or FakeBus(), screen.grab, screen.read, window_title=lambda: screen.title, **kw)


# ------------------------------------------------------------ frame_diff
def test_thumbnail_downsamples_to_gray():
    t = thumbnail(np.zeros((1080, 1920, 4), dtype=np.uint8))
    assert t.shape == (135, 240) and t.dtype == np.uint8


def test_changed_fraction_and_resolution_change():
    a = np.zeros((10, 10), dtype=np.uint8)
    b = a.copy()
    b[0, :5] = 255
    assert changed_fraction(a, b) == pytest.approx(0.05)
    assert changed_fraction(a, np.zeros((5, 5), dtype=np.uint8)) == 1.0


def test_detector_reports_once_per_stable_change():
    d = ChangeDetector(threshold=0.06)
    a, b = thumbnail(frame(0)), thumbnail(frame(200))
    assert d.update(a) is False  # primeiro frame: ainda não sabe se assentou
    assert d.update(a) is True  # tela estável e nova
    assert d.update(a) is False  # a mesma tela não é lida de novo
    assert d.update(b) is False  # mudou, mas ainda está mudando
    assert d.update(b) is True  # assentou
    tiny = b.copy()
    tiny[0, 0] = 0  # 1% da tela (cursor, relógio)
    assert d.update(tiny) is False
    d.reset()
    assert d.update(tiny) is True


# ------------------------------------------------------------------- OCR
def test_clean_ocr_text_drops_noise():
    raw = "  Traceback   (most recent call last):\n\n|| ~= »\na\n— · —\nValueError: deu ruim  "
    assert clean_ocr_text(raw) == "Traceback (most recent call last):\nValueError: deu ruim"
    assert len(clean_ocr_text("palavra " * 500, max_chars=100)) == 100


def test_similarity():
    assert similarity("Arquivo Editar Exibir", "Arquivo Editar Exibír") > 0.9
    assert similarity("Arquivo", "Game Over") < 0.5
    assert similarity("", "") == 1.0 and similarity("", "x") == 0.0


# --------------------------------------------------------------- watcher
@pytest.mark.asyncio
async def test_watcher_publishes_public_event_and_dedupes():
    screen = FakeScreen(title="main.py - Visual Studio Code", text="Traceback (most recent call last):\nValueError: x")
    bus = FakeBus()
    w = watcher(screen, bus)
    seen = await w.step()  # janela nova => lê
    assert seen and "Janela: main.py - Visual Studio Code" in seen and "ValueError" in seen
    ev = bus.events[0]
    assert ev.kind == "ambient.screen" and ev.trust == Trust.PUBLIC
    assert ev.payload["window"] == "main.py - Visual Studio Code"

    # A tela assenta (o detector dispara), mas o texto é o mesmo: não repete.
    screen.text = "Traceback (most recent ca11 last):\nValueError: x"
    assert await w.step() is None
    assert await w.step() is None
    assert len(bus.events) == 1


@pytest.mark.asyncio
async def test_watcher_skips_sensitive_windows_and_pause():
    screen = FakeScreen(title="Bitwarden — Cofre", text="minha senha super secreta 123")
    pause = PauseSwitch()
    w = watcher(screen, pause=pause)
    assert await w.step(force=True) is None
    assert screen.grabs == 0 and screen.reads == 0  # nem tira o print

    screen.title = "Firefox"
    pause.pause("teste")
    assert await w.step(force=True) is None
    assert screen.grabs == 0
    pause.resume()
    assert await w.step() is not None  # saiu da janela sensível: lê a próxima


@pytest.mark.asyncio
async def test_watcher_custom_ignore_list_replaces_default():
    screen = FakeScreen(title="WhatsApp", text="conversa particular com alguém")
    assert await watcher(screen, ignored_windows=["whatsapp"]).step() is None
    screen.title = "Senhas do navegador"
    assert await watcher(screen, ignored_windows=["whatsapp"]).step() is not None


@pytest.mark.asyncio
async def test_watcher_ignores_screens_without_text():
    screen = FakeScreen(title="", text="ok")
    w = watcher(screen)
    assert await w.step(force=True) is None
    screen.text = "||| ~~ ==="
    screen.img = frame(255)
    for _ in range(3):
        assert await w.step() is None
    assert screen.reads >= 2


@pytest.mark.asyncio
async def test_watcher_rate_limits_vision_model():
    calls = []

    async def describe(_img):
        calls.append(1)
        return "Um jogo de plataforma com um personagem pulando."

    screen = FakeScreen(text="")
    w = watcher(screen, describe=describe, describe_interval_s=45)
    for i, t in enumerate([1000, 1010, 1050]):
        screen.title = f"Jogo {i}"  # troca de janela força uma nova olhada
        seen = await w.step(now=t)
        assert (seen is not None) == (t != 1010)  # sem texto e sem descrição: nada
    assert len(calls) == 2
    assert "Cena: Um jogo de plataforma" in seen


@pytest.mark.asyncio
async def test_watcher_survives_ocr_failure():
    async def broken(_img):
        raise RuntimeError("tesseract caiu")

    bus = FakeBus()
    w = ScreenWatcher(bus, lambda: frame(0), broken, window_title=lambda: "Firefox")
    assert await w.step() is None and bus.events == []


# ---------------------------------------------------- imagens nos providers
def client(handler, base):
    return httpx.AsyncClient(base_url=base, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_providers_send_images():
    seen = {}

    def openai(req):
        seen["openai"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "uma tela"}}]})

    def anthropic(req):
        seen["anthropic"] = json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "uma tela"}]})

    msgs = [Message("user", "o que tem aqui?", images=["QUJD"])]
    await OpenAICompatibleProvider("https://x/v1", client=client(openai, "https://x/v1")).complete(msgs, model="m")
    await AnthropicProvider("k", client=client(anthropic, "https://a/v1")).complete(msgs, model="c")

    content = seen["openai"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "o que tem aqui?"}
    assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"
    blocks = seen["anthropic"]["messages"][0]["content"]
    assert blocks[0]["source"] == {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}
    assert blocks[1] == {"type": "text", "text": "o que tem aqui?"}


@pytest.mark.asyncio
async def test_describer_sends_jpeg_to_model():
    pytest.importorskip("PIL")
    from akemi.vision.describe import ScreenDescriber

    llm = EchoProvider(lambda m, j: "  Um editor de código\n aberto. ")
    text = await ScreenDescriber(llm, "vision-model").describe(np.zeros((720, 2560, 3), dtype=np.uint8))
    assert text == "Um editor de código aberto."
    assert llm.calls[0][1][0].images  # a imagem foi junto


# --------------------------------------------------------- ponta a ponta
class RecordingTTS(TTSEngine):
    def __init__(self):
        self.said = []

    async def speak(self, text):
        self.said.append(text)


@pytest.mark.asyncio
async def test_error_on_screen_makes_her_comment(tmp_path):
    def responder(msgs, json_mode):
        if json_mode:
            return '{"decision": "comment"}'
        return "Opa, apareceu um erro aí."

    settings = Settings(
        _env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0,
        min_seconds_between_unprompted_speech=0,
    )
    tts, llm = RecordingTTS(), EchoProvider(responder)
    akemi = Akemi(settings, fast=llm, deep=llm, tts=tts, persist=False)
    await akemi.start()
    screen = FakeScreen(title="cmd.exe", text="Traceback (most recent call last):\nKeyError: 'token'")
    w = ScreenWatcher(akemi.bus, screen.grab, screen.read, akemi.pause, window_title=lambda: screen.title)
    assert await w.step()
    await akemi.bus.drain()
    await asyncio.sleep(0.3)
    context = akemi.mind._context_block()
    await akemi.stop()
    assert tts.said == ["Opa, apareceu um erro aí."]
    assert "[ambient.screen] Janela: cmd.exe" in context
