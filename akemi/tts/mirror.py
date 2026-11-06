"""Voz dela também na chamada: toca no seu fone e, no modo chamada, no cabo virtual.

No Discord (ou outro app), o microfone vira o "CABLE Output" do VB-Cable. A voz
dela entra pelo "CABLE Input" (``CALL_OUTPUT_DEVICE``), e a sua entra pelo mesmo
cabo com o "Escutar este dispositivo" do Windows (veja docs/CAPRI.md). Fora do
modo chamada, nada vai para o cabo.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class CallMirrorPlayer:
    """Embrulha o tocador normal; com ``mirror`` ligado, toca o mesmo áudio no cabo."""

    def __init__(self, main: Any, call_device: str | int | None,
                 make_player: Callable[[str | int | None], Any] | None = None) -> None:
        self.main = main
        self.call_device = call_device
        self._make = make_player
        self._call: Any = None
        self.mirror = False
        self.error = ""

    def _call_player(self) -> Any:
        if self._call is None:
            if self._make is None:
                from .piper_engine import SoundDevicePlayer

                self._make = SoundDevicePlayer
            self._call = self._make(self.call_device)
        return self._call

    def play(self, pcm16: bytes, sample_rate: int, channels: int, cancel: threading.Event) -> None:
        if not self.mirror or self.call_device in (None, ""):
            self.main.play(pcm16, sample_rate, channels, cancel)
            return
        # Os dois ao mesmo tempo: o cabo numa thread, o seu fone nesta.
        try:
            call = self._call_player()
        except Exception as exc:  # cabo sumiu: pelo menos você ouve
            self.error = str(exc)
            self.main.play(pcm16, sample_rate, channels, cancel)
            return
        worker = threading.Thread(target=self._safe_play, args=(call, pcm16, sample_rate, channels, cancel),
                                  daemon=True)
        worker.start()
        self.main.play(pcm16, sample_rate, channels, cancel)
        worker.join()

    def _safe_play(self, player: Any, pcm16: bytes, sample_rate: int, channels: int, cancel: threading.Event) -> None:
        try:
            player.play(pcm16, sample_rate, channels, cancel)
        except Exception as exc:
            self.error = str(exc)

    def close(self) -> None:
        for player in (self.main, self._call):
            close = getattr(player, "close", None)
            if close is not None:
                close()
