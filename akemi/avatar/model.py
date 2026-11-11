"""O que o avatar está fazendo e qual cara fazer: lógica pura, sem Tk.

A janela chama :meth:`AvatarModel.look` a cada quadro e desenha o resultado.
Os eventos (fala, humor, Modo Casca, carinho) entram por :meth:`AvatarModel.on`.
"""

from __future__ import annotations

import math
import random
import textwrap
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

# Cor de destaque por humor (pontas do cabelo, detalhes do moletom, luz do chip).
MOOD_ACCENT = {
    "neutra": "#42D9E8",
    "animada": "#FC3B7F",
    "curiosa": "#7cff6b",
    "focada": "#4f8bff",
    "entediada": "#9a8fb5",
    "irritadinha": "#ff5a3c",
    "sonolenta": "#E9D5FF",
    "orgulhosa": "#ffc93c",
    "triste": "#6f8fc8",
}
# Olhos e boca de cada humor quando ela não está fazendo outra coisa.
MOOD_FACE = {
    "neutra": ("abertos", "sorrisinho"),
    "animada": ("felizes", "sorriso"),
    "curiosa": ("abertos", "o"),
    "focada": ("abertos", "fechada"),
    "entediada": ("entediados", "fechada"),
    "irritadinha": ("bravos", "bico"),
    "sonolenta": ("sonolentos", "fechada"),
    "orgulhosa": ("felizes", "sorriso"),
    "triste": ("entediados", "fechada"),
}
PAT_REVERSALS = 4  # vai e volta do mouse em cima dela para contar como carinho
PAT_WINDOW_S = 1.6
PAT_MIN_STEP_PX = 5
PAT_COOLDOWN_S = 4.0


@dataclass
class Look:
    """Tudo o que o desenho precisa saber num quadro."""

    eyes: str = "abertos"  # abertos | fechados | felizes | bravos | sonolentos | entediados | surpresos
    mouth: str = "sorrisinho"  # fechada | aberta | sorriso | sorrisinho | bico | o
    accent: str = MOOD_ACCENT["neutra"]
    blush: bool = False
    glitch: float = 0.0  # 0..1
    sleeping: bool = False
    bubble: list[str] | None = None  # linhas do balão de fala
    caption: str = ""  # legenda curta (Modo Casca)
    bounce: float = 0.0  # deslocamento vertical, em px
    tilt: float = 0.0  # inclinação da cabeça, em px
    phase: float = 0.0  # relógio para animações (zzz, brilho)


def wrap_bubble(text: str, width: int = 30, max_lines: int = 5) -> list[str]:
    """Quebra o texto em linhas para o balão; corta com reticências se passar."""
    text = " ".join(text.split())
    if not text:
        return []
    lines = textwrap.wrap(text, width=width, break_long_words=True, break_on_hyphens=False)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        lines[-1] = (last[: width - 1].rstrip() if len(last) >= width else last.rstrip(" .,;")) + "…"
    return lines


class AvatarModel:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        sleep_after_s: float = 300.0,
        chars_per_second: float = 15.0,
    ) -> None:
        self.clock = clock
        self.rng = rng or random.Random()
        self.sleep_after_s = sleep_after_s
        self.cps = chars_per_second
        now = clock()
        self.mood = "neutra"
        self.mood_intensity = 0.5
        self.speaking = False
        self._speech_text = ""
        self._speech_started = 0.0
        self._talk_until = 0.0
        self._mouth_open = False
        self._mouth_next = 0.0
        self._bubble: list[str] | None = None
        self._bubble_until = 0.0
        self._caption = ""
        self._caption_until = 0.0
        self._blink_at = now + self.rng.uniform(2.0, 5.0)
        self._blink_until = 0.0
        self._glitch_until = now + 1.2  # chega dando uma falhada
        self._glitch_len = 1.2
        self._listen_until = 0.0
        self._happy_until = 0.0
        self._flinch_until = 0.0
        self._hop_at = -1e9
        self._last_activity = now
        self.forced_sleep = False
        self._pats: deque[tuple[float, int]] = deque()  # (quando, direção)
        self._last_x: int | None = None
        self._last_dir = 0
        self._pat_at = -1e9
        self.pats = 0

    # ------------------------------------------------------------- estado
    @property
    def sleeping(self) -> bool:
        if self.forced_sleep:
            return True
        now = self.clock()
        busy = self.speaking or now < self._talk_until or now < self._bubble_until
        return not busy and now - self._last_activity >= self.sleep_after_s

    def _wake(self) -> None:
        now = self.clock()
        if self.sleeping and not self.forced_sleep:
            self.glitch(0.5)  # acorda com uma falhada
        self._last_activity = now

    def glitch(self, seconds: float = 0.8) -> None:
        now = self.clock()
        self._glitch_until = max(self._glitch_until, now + seconds)
        self._glitch_len = max(seconds, 0.01)

    def sleep(self, on: bool = True) -> None:
        self.forced_sleep = on
        if not on:
            self._last_activity = self.clock()
            self.glitch(0.5)

    # ------------------------------------------------------------- eventos
    def on(self, kind: str, payload: dict | None = None) -> None:
        payload = payload or {}
        now = self.clock()
        text = str(payload.get("text", ""))
        if kind == "mood":
            mood = str(payload.get("mood", "neutra"))
            if mood in MOOD_FACE:
                if mood != self.mood and mood in ("animada", "orgulhosa"):
                    self._hop_at = now
                self.mood = mood
            self.mood_intensity = float(payload.get("intensity", self.mood_intensity))
            return
        if self.forced_sleep and kind not in ("speech.started", "owner.message"):
            return
        if kind in ("speech.started", "owner.message") and self.forced_sleep:
            self.forced_sleep = False
            self.glitch(0.5)
        self._wake()
        if kind == "speech.started":
            self.speaking = True
            self._speech_text = text
            self._speech_started = now
            self._talk_until = 0.0
            self._bubble = wrap_bubble(text)
            self._bubble_until = now + 60.0  # se o fim da fala nunca chegar, some sozinho
        elif kind == "speech.finished":
            self.speaking = False
            spoken = self._speech_text
            # Sem voz (só texto), a fala "termina" na hora: a boca mexe pelo tempo de leitura.
            if now - self._speech_started < 0.4:
                self._talk_until = self._speech_started + min(len(spoken) / self.cps, 6.0)
            reading = max(2.5, min(len(spoken) / self.cps, 10.0))
            self._bubble_until = max(now, self._talk_until) + reading * 0.5
        elif kind in ("owner.speech_started", "owner.message"):
            self._listen_until = now + 2.0
        elif kind.startswith("casca."):
            sub = kind.split(".", 1)[1]
            if text and sub not in ("hi", "bye"):
                self._caption = text if len(text) <= 60 else text[:59].rstrip() + "…"
                self._caption_until = now + (5.0 if sub != "caption" else 4.0)
            if sub == "compact":
                self.glitch(1.0)
            elif sub in ("failure", "error"):
                self._flinch_until = now + 1.2
            elif sub == "done":
                self._happy_until = now + 2.5
                self._hop_at = now
            elif sub in ("approval", "input"):
                self._listen_until = now + 3.0
            elif sub == "agent_start":
                self.glitch(0.35)
        elif kind == "pat":
            self._happy_until = now + 2.5
            self._hop_at = now

    def pointer(self, x: int, inside: bool) -> bool:
        """Movimento do mouse. Vai e volta em cima dela = carinho. Devolve True no carinho."""
        now = self.clock()
        if not inside:
            self._last_x, self._last_dir = None, 0
            self._pats.clear()
            return False
        if self._last_x is None:
            self._last_x = x
            return False
        step = x - self._last_x
        if abs(step) < PAT_MIN_STEP_PX:
            return False
        self._last_x = x
        direction = 1 if step > 0 else -1
        if self._last_dir and direction != self._last_dir:
            self._pats.append((now, direction))
        self._last_dir = direction
        while self._pats and now - self._pats[0][0] > PAT_WINDOW_S:
            self._pats.popleft()
        if len(self._pats) >= PAT_REVERSALS and now - self._pat_at >= PAT_COOLDOWN_S:
            self._pats.clear()
            self._pat_at = now
            self.pats += 1
            self.on("pat")
            return True
        return False

    # -------------------------------------------------------------- quadro
    def _blink(self, now: float) -> bool:
        if now >= self._blink_at:
            self._blink_until = now + 0.12
            gap = self.rng.uniform(2.5, 6.0)
            if self.rng.random() < 0.2:
                gap = 0.25  # piscada dupla
            self._blink_at = now + gap
        return now < self._blink_until

    def _mouth(self, now: float, talking: bool, resting: str) -> str:
        if not talking:
            self._mouth_open = False
            return resting
        if now >= self._mouth_next:
            self._mouth_open = not self._mouth_open
            self._mouth_next = now + self.rng.uniform(0.09, 0.17)
        return "aberta" if self._mouth_open else "fechada"

    def look(self) -> Look:
        now = self.clock()
        eyes, mouth = MOOD_FACE.get(self.mood, MOOD_FACE["neutra"])
        look = Look(accent=MOOD_ACCENT.get(self.mood, MOOD_ACCENT["neutra"]), phase=now)
        if now >= self._caption_until:
            self._caption = ""
        look.caption = self._caption
        if self._bubble and now < self._bubble_until:
            look.bubble = self._bubble
        elif now >= self._bubble_until:
            self._bubble = None
        if now < self._glitch_until:
            look.glitch = max(0.0, min(1.0, (self._glitch_until - now) / self._glitch_len))
        if self.sleeping:
            look.sleeping = True
            look.eyes, look.mouth = "fechados", "fechada"
            look.bounce = 1.5 * math.sin(now * 1.2)  # respiração lenta
            look.caption = ""
            return look
        talking = self.speaking or now < self._talk_until
        if now < self._flinch_until:
            eyes, mouth = "surpresos", "o"
        elif now < self._happy_until:
            eyes, mouth = "felizes", "sorriso"
            look.blush = True
        elif now < self._listen_until:
            eyes = "surpresos" if eyes in ("abertos", "entediados", "sonolentos") else eyes
            look.tilt = 3.0
        if self.mood == "orgulhosa" or (self.mood == "animada" and self.mood_intensity > 0.7):
            look.blush = True
        look.eyes = "fechados" if self._blink(now) and eyes not in ("felizes", "surpresos") else eyes
        look.mouth = self._mouth(now, talking, mouth)
        look.bounce = 1.2 * math.sin(now * 2.0)  # respirando
        since_hop = now - self._hop_at
        if 0 <= since_hop < 0.45:
            look.bounce -= 10 * math.sin(math.pi * since_hop / 0.45)
        if talking:
            look.bounce += 0.8 * math.sin(now * 9.0)
        return look
