"""Desenha a Akemi num canvas do Tk a partir de um :class:`~akemi.avatar.model.Look`.

O visual é próprio da Akemi: cabelo roxo com as pontas na cor do humor, olhos
ciano, moletom rosa e uma presilha em forma de chip (o chip de identidade do
Projeto Capricórnio). Tudo em formas simples, sem imagens.

Skin: uma pasta com PNGs (``AVATAR_SKIN``) troca o desenho por imagens suas.
Precisa de ``parado.png``; ``piscando.png``, ``falando.png``, ``feliz.png`` e
``dormindo.png`` são opcionais (sem elas, usa a ``parado.png``). Use só arte que
você tem direito de usar.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .model import Look

# Paleta da Akemi: rosa vibrante (principal), roxo profundo (contraste),
# azul-ciano (detalhes), lilás pastel (suavidade) e creme (partes neutras).
PINK = "#FC3B7F"
PURPLE = "#5420A8"
CYAN = "#42D9E8"
LILAC = "#E9D5FF"
CREAM = "#FFF1E6"

SKIN = "#f7dccb"
SKIN_SHADE = "#eec2ac"
HAIR = PURPLE
HAIR_DARK = "#3a1578"
HOODIE = PINK
HOODIE_DARK = "#c92463"
LEGS = "#2e1260"
SHOES = CREAM
IRIS = CYAN
PUPIL = "#3a1578"
LINE = "#24103f"
MOUTH = "#8a2c3d"
BLUSH = "#ff8fb5"
CHIP = CREAM
CHIP_PINS = LILAC
BUBBLE_BG = CREAM
BUBBLE_TEXT = "#24103f"
CAPTION_BG = "#24103f"
CAPTION_TEXT = LILAC
SKIN_FRAMES = ("parado", "piscando", "falando", "feliz", "dormindo")

# Tamanho do canvas em unidades (multiplicado pela escala).
WIDTH = 260
HEIGHT = 300
AVATAR_HEIGHT = 170  # dos pés ao topo do cabelo


def canvas_size(scale: float) -> tuple[int, int]:
    return round(WIDTH * scale), round(HEIGHT * scale)


def load_skin(folder: Path, tk_photo: Callable[..., Any], *, scale: float = 1.0) -> dict[str, Any]:
    """Carrega os PNGs da skin. Com Pillow, endurece a transparência (sem borda colorida)."""
    frames: dict[str, Any] = {}
    if not (folder / "parado.png").is_file():
        raise FileNotFoundError(f"a skin precisa de {folder / 'parado.png'}")
    for name in SKIN_FRAMES:
        path = folder / f"{name}.png"
        if not path.is_file():
            continue
        try:
            from PIL import Image, ImageTk

            img = Image.open(path).convert("RGBA")
            target = round(AVATAR_HEIGHT * 1.15 * scale)
            if img.height != target:
                img = img.resize((max(1, round(img.width * target / img.height)), target), Image.LANCZOS)
            alpha = img.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
            img.putalpha(alpha)
            frames[name] = ImageTk.PhotoImage(img)
        except ImportError:
            frames[name] = tk_photo(file=str(path))
    return frames


def skin_frame(look: Look, frames: dict[str, Any]) -> Any:
    if look.sleeping and "dormindo" in frames:
        return frames["dormindo"]
    if look.mouth == "aberta" and "falando" in frames:
        return frames["falando"]
    if look.eyes == "fechados" and "piscando" in frames:
        return frames["piscando"]
    if look.eyes == "felizes" and "feliz" in frames:
        return frames["feliz"]
    return frames["parado"]


class Painter:
    """Desenha um quadro. ``measure(texto, fonte)`` mede a largura em pixels."""

    def __init__(
        self,
        canvas: Any,
        scale: float = 1.0,
        *,
        bg: str = "#ff00fe",
        skin: dict[str, Any] | None = None,
        measure: Callable[[str, Any], int] | None = None,
        font: Any = ("Segoe UI", 10),
        small_font: Any = ("Segoe UI", 8, "bold"),
    ) -> None:
        self.c = canvas
        self.s = scale
        self.bg = bg
        self.skin = skin or {}
        self.measure = measure or (lambda text, _font: int(len(text) * 6.6 * scale))
        self.font = font
        self.small_font = small_font
        self.width, self.height = canvas_size(scale)
        self.cx = self.width / 2

    # ------------------------------------------------------------ helpers
    def p(self, x: float, y: float, dy: float = 0.0) -> tuple[float, float]:
        """De coordenadas da Akemi (pés em 0,0; y negativo para cima) para o canvas."""
        return self.cx + x * self.s, self.height - 4 * self.s + (y + dy) * self.s

    def pts(self, coords: list[tuple[float, float]], dy: float = 0.0) -> list[float]:
        out: list[float] = []
        for x, y in coords:
            out.extend(self.p(x, y, dy))
        return out

    def oval(self, x: float, y: float, rx: float, ry: float, dy: float = 0.0, **kw) -> None:
        x0, y0 = self.p(x - rx, y - ry, dy)
        x1, y1 = self.p(x + rx, y + ry, dy)
        self.c.create_oval(x0, y0, x1, y1, **kw)

    def poly(self, coords: list[tuple[float, float]], dy: float = 0.0, **kw) -> None:
        self.c.create_polygon(*self.pts(coords, dy), **kw)

    def line(self, coords: list[tuple[float, float]], dy: float = 0.0, width: float = 2, **kw) -> None:
        self.c.create_line(*self.pts(coords, dy), width=max(1, width * self.s), capstyle="round",
                           joinstyle="round", **kw)

    def round_rect(self, x0: float, y0: float, x1: float, y1: float, r: float, **kw) -> None:
        """Retângulo arredondado em pixels do canvas."""
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        self.c.create_polygon(*pts, smooth=True, **kw)

    # ------------------------------------------------------------ quadro
    def draw(self, look: Look) -> None:
        self.c.delete("all")
        dy = look.bounce
        if self.skin:
            x, y = self.p(0, 0, dy)
            self.c.create_image(x, y, image=skin_frame(look, self.skin), anchor="s")
            top = -AVATAR_HEIGHT * 1.15
        else:
            self._body(look, dy)
            self._head(look, dy)
            top = -AVATAR_HEIGHT
        if look.sleeping:
            self._zzz(look, dy)
        if look.glitch > 0:
            self._glitch(look, top)
        above = top - 8
        if look.caption:
            above = self._caption(look, above)
        if look.bubble:
            self._bubble(look, above)

    def _body(self, look: Look, dy: float) -> None:
        a = look.accent
        # pernas balançando (sentada na borda da janela)
        swing = 2.0 * (1 if int(look.phase * 1.3) % 2 else -1) if not look.sleeping else 0.0
        for side in (-1, 1):
            x = side * 9
            self.poly([(x - 5, -40), (x + 5, -40), (x + 5 + swing * side, -6), (x - 5 + swing * side, -6)],
                      dy, fill=LEGS, outline=LINE, width=1)
            self.oval(x + swing * side, -4, 7, 4.5, dy, fill=SHOES, outline=LINE)
        # barra do moletom
        self.poly([(-25, -66), (25, -66), (31, -36), (-31, -36)], dy, fill=HOODIE, outline=LINE, width=1)
        # faixa creme por baixo: o detalhe aparece mesmo quando o humor é rosa
        self.line([(-30, -38), (30, -38)], dy, width=4.6, fill=CREAM)
        self.line([(-30, -38), (30, -38)], dy, width=2.2, fill=a)
        # tronco
        self.poly([(-22, -94), (22, -94), (26, -64), (-26, -64)], dy, fill=HOODIE, outline=LINE, width=1)
        self.line([(0, -92), (0, -40)], dy, width=1.4, fill=HOODIE_DARK)
        self.oval(0, -70, 4.2, 4.2, dy, fill=a, outline=CREAM, width=2)  # o "led" do moletom
        # braços e mãos
        for side in (-1, 1):
            self.poly([(side * 21, -92), (side * 31, -78), (side * 30, -54), (side * 23, -54), (side * 20, -78)],
                      dy, fill=HOODIE, outline=LINE, width=1)
            self.oval(side * 27, -50, 5.5, 5, dy, fill=SKIN, outline=SKIN_SHADE)
        # pescoço e capuz
        self.poly([(-26, -98), (26, -98), (20, -88), (-20, -88)], dy, fill=HOODIE_DARK, outline=LINE, width=1)
        self.poly([(-6, -100), (6, -100), (5, -92), (-5, -92)], dy, fill=SKIN_SHADE, outline="")

    def _head(self, look: Look, dy: float) -> None:
        a = look.accent
        t = look.tilt
        hy = -126
        # cabelo de trás e mechas laterais (pontas na cor do humor)
        self.oval(t, hy, 47, 45, dy, fill=HAIR, outline=LINE, width=1)
        for side in (-1, 1):
            # mecha comprida que desce até o ombro e termina em ponta colorida
            lock = [(side * 33 + t, hy - 8), (side * 45 + t, hy + 8), (side * 43 + t, hy + 38),
                    (side * 40 + t, hy + 50), (side * 32 + t, hy + 38), (side * 30 + t, hy + 14)]
            self.poly(lock, dy, fill=HAIR, outline=LINE, width=1, smooth=True)
            tip = [(side * 43 + t, hy + 36), (side * 38 + t, hy + 64), (side * 31 + t, hy + 36)]
            self.poly(tip, dy, fill=a, outline=LINE, width=1)
        # rosto
        self.oval(t, hy + 4, 37, 35, dy, fill=SKIN, outline=SKIN_SHADE, width=1)
        self._eyes(look, dy, hy)
        self._mouth(look, dy, hy)
        if look.blush:
            for side in (-1, 1):
                self.oval(side * 22 + t, hy + 18, 6.5, 3.2, dy, fill=BLUSH, outline="")
        # franja
        bangs = [(-40 + t, hy - 2), (-36 + t, hy - 30), (-14 + t, hy - 44), (14 + t, hy - 44), (36 + t, hy - 30),
                 (40 + t, hy - 2), (30 + t, hy - 12), (24 + t, hy - 4), (16 + t, hy - 16), (6 + t, hy - 8),
                 (-4 + t, hy - 18), (-14 + t, hy - 6), (-22 + t, hy - 16), (-30 + t, hy - 4)]
        self.poly(bangs, dy, fill=HAIR, outline=LINE, width=1, smooth=True)
        self.line([(-10 + t, hy - 38), (2 + t, hy - 26)], dy, width=1.6, fill=HAIR_DARK)
        # presilha de chip, com a luz na cor do humor
        cx, cy = 26 + t, hy - 26
        self.poly([(cx - 7, cy - 4), (cx + 7, cy - 6), (cx + 8, cy + 2), (cx - 6, cy + 4)], dy,
                  fill=CHIP, outline=LINE, width=1)
        for i in (-4, 0, 4):
            self.line([(cx + i, cy + 4), (cx + i, cy + 7)], dy, width=1, fill=CHIP_PINS)
        self.oval(cx + 1, cy - 1, 2.2, 2.2, dy, fill=a, outline=a)

    def _eyes(self, look: Look, dy: float, hy: float) -> None:
        t = look.tilt
        ey = hy + 6
        kind = look.eyes
        for side in (-1, 1):
            x = side * 15 + t
            if kind == "fechados":
                self.line([(x - 8, ey + 1), (x, ey + 4), (x + 8, ey + 1)], dy, width=2.2, fill=LINE, smooth=True)
                continue
            if kind == "felizes":
                self.line([(x - 8, ey + 3), (x, ey - 4), (x + 8, ey + 3)], dy, width=2.4, fill=LINE, smooth=True)
                continue
            big = 1.15 if kind == "surpresos" else 1.0
            self.oval(x, ey, 8 * big, 10 * big, dy, fill="white", outline=LINE, width=1)
            self.oval(x, ey + 1, 6.2 * big, 8 * big, dy, fill=IRIS, outline="")
            pupil = 2.4 if kind == "surpresos" else 3.6
            self.oval(x, ey + 2, pupil, pupil * 1.3, dy, fill=PUPIL, outline="")
            self.oval(x - 2.6, ey - 3, 2.2, 2.2, dy, fill="white", outline="")
            # cílios de cima
            self.line([(x - 9, ey - 6), (x - 3, ey - 10), (x + 4, ey - 10), (x + 9, ey - 5)], dy,
                      width=2.4, fill=LINE, smooth=True)
            if kind in ("sonolentos", "entediados"):
                cover = 6 if kind == "sonolentos" else 3
                self.poly([(x - 10, ey - 12), (x + 10, ey - 12), (x + 10, ey - 3 + cover), (x - 10, ey - 3 + cover)],
                          dy, fill=SKIN, outline="")
                self.line([(x - 9, ey - 3 + cover), (x + 9, ey - 3 + cover)], dy, width=2.2, fill=LINE)
            elif kind == "bravos":
                inner = x - side * 9
                outer = x + side * 10
                self.poly([(inner, ey - 2), (outer, ey - 9), (outer, ey - 13), (inner, ey - 13)], dy, fill=SKIN, outline="")
                self.line([(inner, ey - 2), (outer, ey - 9)], dy, width=2.4, fill=LINE)

    def _mouth(self, look: Look, dy: float, hy: float) -> None:
        x, y = look.tilt, hy + 22
        kind = look.mouth
        if kind == "aberta":
            self.oval(x, y, 5, 4.2, dy, fill=MOUTH, outline=LINE, width=1)
            self.oval(x, y + 2, 3, 1.6, dy, fill="#e8738a", outline="")
        elif kind == "sorriso":
            self.poly([(x - 7, y - 2), (x + 7, y - 2), (x + 4, y + 4), (x - 4, y + 4)], dy,
                      fill=MOUTH, outline=LINE, width=1, smooth=True)
        elif kind == "sorrisinho":
            self.line([(x - 5, y - 1), (x, y + 2), (x + 5, y - 1)], dy, width=1.8, fill=LINE, smooth=True)
        elif kind == "bico":
            self.line([(x - 5, y + 1), (x - 2, y - 1), (x + 1, y + 1), (x + 4, y - 1)], dy, width=1.8, fill=LINE, smooth=True)
        elif kind == "o":
            self.oval(x, y, 2.6, 3, dy, fill=MOUTH, outline=LINE, width=1)
        else:
            self.line([(x - 4, y), (x + 4, y)], dy, width=1.8, fill=LINE)

    def _zzz(self, look: Look, dy: float) -> None:
        for i, size in enumerate((9, 12, 15)):
            phase = (look.phase * 0.4 + i / 3) % 1.0
            x, y = self.p(46 + i * 12 + phase * 8, -150 - i * 14 - phase * 16, dy)
            self.c.create_text(x, y, text="z" if i < 2 else "Z", fill=look.accent,
                               font=("Segoe UI", max(6, round(size * self.s)), "bold"))

    def _glitch(self, look: Look, top: float) -> None:
        """Faixas deslocadas, como uma imagem falhando."""
        rng = random.Random(int(look.phase * 24))
        bands = 3 + round(look.glitch * 6)
        y0 = self.p(0, top)[1]
        y1 = self.p(0, 0)[1]
        for _ in range(bands):
            y = rng.uniform(y0, y1)
            h = rng.uniform(2, 7) * self.s
            shift = rng.uniform(-14, 14) * self.s * look.glitch
            x0 = self.cx - 50 * self.s + shift
            x1 = self.cx + 50 * self.s + shift
            color = rng.choice((look.accent, CYAN, PINK, self.bg))
            self.c.create_rectangle(x0, y, x1, y + h, fill=color, outline="")

    def _caption(self, look: Look, above: float) -> float:
        text = look.caption
        w = self.measure(text, self.small_font) + 16 * self.s
        w = min(w, self.width - 4)
        _, y = self.p(0, above)
        h = 18 * self.s
        x0 = self.cx - w / 2
        self.round_rect(x0, y - h, x0 + w, y, 8 * self.s, fill=CAPTION_BG, outline=look.accent, width=1)
        self.c.create_text(self.cx, y - h / 2, text=text, fill=CAPTION_TEXT, font=self.small_font, width=w - 8)
        return above - 22

    def _bubble(self, look: Look, above: float) -> None:
        lines = look.bubble or []
        text = "\n".join(lines)
        widest = max((self.measure(line, self.font) for line in lines), default=0)
        pad = 9 * self.s
        line_h = 16 * self.s
        w = min(widest + pad * 2, self.width - 4)
        h = line_h * len(lines) + pad * 2
        _, bottom = self.p(0, above)
        bottom -= 9 * self.s  # espaço para o rabinho
        top = max(1, bottom - h)
        x0 = self.cx - w / 2
        x1 = self.cx + w / 2
        self.round_rect(x0, top, x1, bottom, 10 * self.s, fill=BUBBLE_BG, outline=look.accent, width=2)
        tail = [self.cx - 7 * self.s, bottom - 1, self.cx + 5 * self.s, bottom - 1, self.cx - 2 * self.s, bottom + 9 * self.s]
        self.c.create_polygon(*tail, fill=BUBBLE_BG, outline="")
        self.c.create_line(self.cx - 7 * self.s, bottom, self.cx - 2 * self.s, bottom + 9 * self.s,
                           self.cx + 5 * self.s, bottom, fill=look.accent, width=2)
        self.c.create_text(self.cx, (top + bottom) / 2, text=text, fill=BUBBLE_TEXT, font=self.font,
                           justify="center", width=w - pad)
