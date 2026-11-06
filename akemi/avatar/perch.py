"""Onde o avatar senta: no topo da janela em foco ou, sem janela boa, no canto da tela.

Tudo aqui é conta pura, para dar para testar sem tela. As funções que perguntam
ao Windows qual janela está em foco ficam no fim e só funcionam lá.
"""

from __future__ import annotations

import sys
from typing import NamedTuple


class Rect(NamedTuple):
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


class Spot(NamedTuple):
    x: int
    y: int
    on_window: bool


SIT_DEPTH = 0.06  # quanto ela "afunda" na borda da janela (fração da altura)


def perch_spot(
    window: Rect | None,
    work: Rect,
    size: tuple[int, int],
    *,
    margin: int = 16,
    side: str = "direita",
) -> Spot:
    """Posição (canto superior esquerdo da janela do avatar) para sentar.

    ``size`` é a janela do avatar inteira (largura, altura): os pés dela ficam na
    borda de baixo. ``work`` é a área útil da tela (sem a barra de tarefas).
    """
    w, h = size
    sink = int(h * SIT_DEPTH)
    if window is not None and _good_window(window, work, w, h):
        if side == "esquerda":
            x = window.left + margin
        else:
            x = window.right - w - margin
        x = max(work.left, min(x, work.right - w))
        return Spot(x, window.top - h + sink, True)
    x = work.left + margin if side == "esquerda" else work.right - w - margin
    return Spot(x, work.bottom - h, False)


def _good_window(win: Rect, work: Rect, w: int, h: int) -> bool:
    """Dá para sentar nessa janela? (larga o bastante, visível, não ocupa a tela toda)."""
    if win.width < w * 0.8 or win.height < 80:
        return False
    if win.top - h < work.top:  # não cabe em cima (maximizada ou colada no topo)
        return False
    if win.bottom <= work.top or win.top >= work.bottom or win.right <= work.left or win.left >= work.right:
        return False
    covers = win.left <= work.left + 4 and win.right >= work.right - 4 and win.top <= work.top + 4
    return not covers


def step_towards(current: tuple[int, int], target: tuple[int, int], *, teleport_px: int = 450) -> tuple[tuple[int, int], bool]:
    """Um passo até o lugar novo. Longe demais, ela "teleporta" (com uma falhada)."""
    cx, cy = current
    tx, ty = target
    dx, dy = tx - cx, ty - cy
    if abs(dx) + abs(dy) > teleport_px:
        return (tx, ty), True
    if abs(dx) <= 2 and abs(dy) <= 2:
        return (tx, ty), False

    def step(d: int) -> int:
        return round(d * 0.22) or (d > 0) - (d < 0)  # pelo menos 1 px, na direção certa

    return (cx + step(dx), cy + step(dy)), False


# ------------------------------------------------------------- Windows
def _win32():  # pragma: no cover - só no Windows
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    return ctypes, wintypes, user32


def work_area() -> Rect | None:  # pragma: no cover - só no Windows
    """A área útil da tela principal (sem a barra de tarefas)."""
    if sys.platform != "win32":
        return None
    ctypes, wintypes, user32 = _win32()
    rect = wintypes.RECT()
    if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
        return None
    return Rect(rect.left, rect.top, rect.right, rect.bottom)


def foreground_window(ignore: set[int] | None = None) -> Rect | None:  # pragma: no cover - só no Windows
    """O retângulo da janela em foco (sem as bordas invisíveis do Windows 10/11)."""
    if sys.platform != "win32":
        return None
    ctypes, wintypes, user32 = _win32()
    hwnd = user32.GetForegroundWindow()
    if not hwnd or (ignore and int(hwnd) in ignore):
        return None
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        return None
    rect = wintypes.RECT()
    try:
        dwm = ctypes.windll.dwmapi
        ok = dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0  # EXTENDED_FRAME_BOUNDS
    except (AttributeError, OSError):
        ok = False
    if not ok and not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    # A área de trabalho e a barra de tarefas não são janelas para sentar.
    name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, name, 64)
    if name.value in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return None
    return Rect(rect.left, rect.top, rect.right, rect.bottom)
