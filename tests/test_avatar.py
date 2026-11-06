"""Avatar de desktop: o que ela faz (modelo), onde senta e o desenho (com um canvas falso)."""

import random

import pytest

from akemi.avatar.draw import Painter, canvas_size, skin_frame
from akemi.avatar.model import MOOD_ACCENT, AvatarModel, Look, wrap_bubble
from akemi.avatar.perch import Rect, perch_spot, step_towards


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def model(**kw):
    clock = Clock()
    m = AvatarModel(clock=clock, rng=random.Random(1), **kw)
    clock.t += 2  # passa a falhada de chegada
    return m, clock


def test_arrives_glitching_then_settles():
    clock = Clock()
    m = AvatarModel(clock=clock, rng=random.Random(1))
    assert m.look().glitch > 0
    clock.t += 2
    assert m.look().glitch == 0


def test_mouth_moves_while_speaking_and_bubble_stays_a_bit():
    m, clock = model()
    m.on("speech.started", {"text": "Oi, tudo bem com você hoje?"})
    mouths = set()
    for _ in range(40):
        clock.t += 0.05
        mouths.add(m.look().mouth)
    assert {"aberta", "fechada"} <= mouths
    assert m.look().bubble == ["Oi, tudo bem com você hoje?"]
    clock.t += 1
    m.on("speech.finished")
    assert m.look().mouth != "aberta" or m.look().mouth  # a boca para
    assert m.look().bubble  # balão continua para dar tempo de ler
    clock.t += 10
    assert m.look().bubble is None


def test_text_only_speech_still_moves_the_mouth():
    """Sem voz, a fala termina na hora: a boca mexe pelo tempo de leitura."""
    m, clock = model()
    m.on("speech.started", {"text": "x" * 45})
    m.on("speech.finished")
    mouths = set()
    for _ in range(30):
        clock.t += 0.05
        mouths.add(m.look().mouth)
    assert "aberta" in mouths
    clock.t += 5
    assert m.look().mouth != "aberta"


def test_blinks_from_time_to_time():
    m, clock = model()
    eyes = []
    for _ in range(400):
        clock.t += 0.03
        eyes.append(m.look().eyes)
    assert "fechados" in eyes and eyes.count("abertos") > eyes.count("fechados") * 5


def test_mood_changes_face_and_color():
    m, clock = model()
    m.on("mood", {"mood": "irritadinha", "intensity": 0.8})
    look = m.look()
    assert look.accent == MOOD_ACCENT["irritadinha"] and look.mouth == "bico"
    m.on("mood", {"mood": "inventado"})
    assert m.mood == "irritadinha"


def test_naps_when_nothing_happens_and_wakes_up():
    m, clock = model(sleep_after_s=60)
    clock.t += 61
    assert m.look().sleeping
    m.on("owner.message", {"text": "Akemi?"})
    look = m.look()
    assert not look.sleeping and look.glitch > 0  # acorda com uma falhada


def test_forced_sleep_ignores_noise_but_wakes_to_speak():
    m, clock = model()
    m.sleep(True)
    m.on("casca.caption", {"text": "Lendo a.py"})
    assert m.look().sleeping and m.look().caption == ""
    m.on("speech.started", {"text": "Oi"})
    assert not m.look().sleeping


def test_casca_reactions():
    m, clock = model()
    m.on("casca.caption", {"text": "Editando mind.py"})
    assert m.look().caption == "Editando mind.py"
    m.on("casca.compact", {"text": "Lá se vai a memória do Claude."})
    assert m.look().glitch > 0
    clock.t += 2
    m.on("casca.failure", {"text": "O Claude tropeçou num erro."})
    assert m.look().eyes == "surpresos"
    clock.t += 2
    m.on("casca.done", {"text": "O Claude terminou."})
    assert m.look().eyes == "felizes" and m.look().blush
    clock.t += 6
    assert m.look().caption == ""


def test_head_pats():
    m, clock = model()
    assert not m.pointer(100, True)
    hits = []
    for x in (120, 100, 120, 100, 120, 100):
        clock.t += 0.1
        hits.append(m.pointer(x, True))
    assert hits.count(True) == 1 and m.pats == 1
    assert m.look().eyes == "felizes"
    # sair de cima zera; tremidinha pequena não conta
    m.pointer(0, False)
    clock.t += 10
    for x in (100, 102, 100, 102, 100, 102, 100):
        assert not m.pointer(x, True)


def test_wrap_bubble():
    assert wrap_bubble("") == []
    lines = wrap_bubble("palavra " * 60, width=20, max_lines=3)
    assert len(lines) == 3 and lines[-1].endswith("…")
    assert all(len(line) <= 20 for line in lines)


# ------------------------------------------------------------------ perch
WORK = Rect(0, 0, 1920, 1040)


def test_sits_on_top_of_the_window():
    spot = perch_spot(Rect(400, 300, 1200, 900), WORK, (260, 300))
    assert spot.on_window and spot.x == 1200 - 260 - 16
    assert spot.y + 300 > 300 > spot.y  # os pés na borda de cima da janela


def test_falls_back_to_the_corner():
    for window in (None, Rect(0, 0, 1920, 1040), Rect(400, 100, 1200, 900), Rect(400, 500, 450, 900)):
        spot = perch_spot(window, WORK, (260, 300))
        assert not spot.on_window and spot.y == 1040 - 300 and spot.x == 1920 - 260 - 16
    left = perch_spot(None, WORK, (260, 300), side="esquerda")
    assert left.x == 16


def test_stays_on_screen_near_the_edge():
    spot = perch_spot(Rect(-300, 400, 200, 900), WORK, (260, 300), side="esquerda")
    assert spot.x >= 0


def test_walks_and_teleports():
    pos, jumped = step_towards((0, 0), (100, 0))
    assert 0 < pos[0] < 100 and not jumped
    pos, jumped = step_towards((0, 0), (1000, 500))
    assert pos == (1000, 500) and jumped
    pos, _ = step_towards((99, 0), (100, 0))
    assert pos == (100, 0)
    pos, _ = step_towards((10, 10), (9, 12))
    assert pos == (9, 12)  # pertinho: encaixa


# ------------------------------------------------------------------ draw
class FakeCanvas:
    def __init__(self) -> None:
        self.items = []

    def delete(self, *_):
        self.items.clear()

    def __getattr__(self, name):
        if name.startswith("create_"):
            return lambda *a, **k: self.items.append((name, a, k))
        raise AttributeError(name)


@pytest.mark.parametrize("look", [
    Look(),
    Look(eyes="fechados", mouth="aberta", bubble=["Oi!"], caption="Editando a.py"),
    Look(eyes="bravos", mouth="bico", blush=True, glitch=0.8, phase=2.0),
    Look(eyes="sonolentos", sleeping=True), Look(eyes="entediados", mouth="o"),
    Look(eyes="felizes", mouth="sorriso", tilt=3, bounce=-6), Look(eyes="surpresos", mouth="sorrisinho"),
])
def test_painter_draws_every_face(look):
    c = FakeCanvas()
    Painter(c, 1.0).draw(look)
    kinds = {k for k, _, _ in c.items}
    assert {"create_oval", "create_polygon"} <= kinds
    texts = [k["text"] for name, _, k in c.items if name == "create_text"]
    if look.bubble:
        assert "Oi!" in texts and "Editando a.py" in texts
    if look.sleeping:
        assert "Z" in texts


def test_painter_stays_inside_the_canvas():
    c = FakeCanvas()
    p = Painter(c, 1.5)
    p.draw(Look(bubble=wrap_bubble("texto bem comprido " * 10), caption="Rodando pytest"))
    w, h = canvas_size(1.5)
    for name, args, _ in c.items:
        if name in ("create_oval", "create_rectangle", "create_text") and args:
            xs, ys = args[0::2], args[1::2]
            assert all(-60 <= x <= w + 60 for x in xs) and all(-1 <= y <= h + 1 for y in ys), (name, args)


def test_skin_frames():
    frames = {"parado": "P", "falando": "F", "dormindo": "D"}
    assert skin_frame(Look(), frames) == "P"
    assert skin_frame(Look(mouth="aberta"), frames) == "F"
    assert skin_frame(Look(sleeping=True), frames) == "D"
    assert skin_frame(Look(eyes="fechados"), frames) == "P"  # sem piscando.png
    c = FakeCanvas()
    Painter(c, 1.0, skin=frames).draw(Look(mouth="aberta", caption="oi"))
    assert any(name == "create_image" and k["image"] == "F" for name, _, k in c.items)


# ------------------------------------------------------------- ponte e corpo
class FakeWindow:
    def __init__(self) -> None:
        self.posted = []
        self.mood = self.on_pat = self.on_menu = None

    def post(self, kind, payload=None):
        self.posted.append((kind, payload))


@pytest.mark.asyncio
async def test_bridge_forwards_events_and_pats():
    import asyncio

    from akemi.avatar.bridge import AvatarBridge
    from akemi.core.events import Event, EventBus

    bus = EventBus()
    bus.start()
    window, pats = FakeWindow(), []
    bus.subscribe("avatar.carinho", pats.append)
    AvatarBridge(bus, window, mood=lambda: ("animada", 0.5)).register()
    assert window.mood() == ("animada", 0.5)
    bus.publish(Event("speech.started", {"text": "oi"}))
    bus.publish(Event("casca.done", {"text": "O Claude terminou."}))
    bus.publish(Event("chat.message", {"text": "isso não vai para o avatar"}))
    await bus.drain()
    assert window.posted == [("speech.started", {"text": "oi"}), ("casca.done", {"text": "O Claude terminou."})]
    await asyncio.to_thread(window.on_pat)  # o carinho vem da thread do Tk
    await bus.drain()
    assert len(pats) == 1
    await bus.stop()


@pytest.mark.asyncio
async def test_body_voice_tells_the_avatar_when_she_speaks():
    import asyncio

    from akemi.core.events import EventBus
    from akemi.net.body import BodyVoice
    from akemi.tts.base import ConsoleTTS

    bus = EventBus()
    bus.start()
    seen, spoken = [], []
    bus.subscribe("speech.*", lambda e: seen.append(e.kind))
    voice = BodyVoice(ConsoleTTS(printer=lambda t: None), lambda sid, status: spoken.append(status), bus)
    voice.start()
    voice.enqueue(1, "oi")
    await asyncio.sleep(0.1)
    await bus.drain()
    assert seen == ["speech.started", "speech.finished"] and spoken == ["done"]
    await voice.stop()
    await bus.stop()
