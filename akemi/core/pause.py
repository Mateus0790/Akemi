"""Interruptor local de pausa/consentimento.

Quando pausada, a Akemi para de capturar áudio/tela e de falar sozinha,
mas continua respondendo comandos diretos do dono (para poder ser despausada).
"""

from __future__ import annotations

import threading
import time


class PauseSwitch:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paused = False
        self._until: float | None = None
        self.reason = ""

    def pause(self, reason: str = "", seconds: float | None = None) -> None:
        with self._lock:
            self._paused = True
            self.reason = reason
            self._until = time.monotonic() + seconds if seconds else None

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._until = None
            self.reason = ""

    @property
    def paused(self) -> bool:
        with self._lock:
            if self._paused and self._until is not None and time.monotonic() >= self._until:
                self._paused = False
                self._until = None
            return self._paused
