"""Captura da tela (``mss``) e título da janela em foco."""

from __future__ import annotations

import sys

import numpy as np


class ScreenGrabber:
    """Captura um monitor como array RGB ``(H, W, 3)``.

    ``monitor`` segue a numeração do ``mss``: 1 = monitor principal, 2 = o
    segundo, ... (0 = todos juntos).
    """

    def __init__(self, monitor: int = 1) -> None:
        try:
            import mss
        except ImportError as exc:
            raise RuntimeError('Captura de tela precisa do mss: pip install -e ".[vision]"') from exc
        self._mss = mss.mss()
        if not 0 <= monitor < len(self._mss.monitors):
            raise RuntimeError(f"monitor {monitor} não existe (há {len(self._mss.monitors) - 1})")
        self.monitor = self._mss.monitors[monitor]

    def grab(self) -> np.ndarray:
        shot = self._mss.grab(self.monitor)
        bgra = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        return bgra[..., 2::-1]  # BGRA -> RGB

    def close(self) -> None:
        self._mss.close()


def active_window_title() -> str:
    """Título da janela em primeiro plano (só Windows; vazio nos outros)."""
    if sys.platform != "win32":
        return ""
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()
