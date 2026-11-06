"""Áudio: captura, VAD, processamento e reconhecimento da voz do dono.

As partes carregam sob demanda. A captura e o VAD precisam dos extras de áudio
(``webrtcvad``, ``PyAudioWPatch``), que o núcleo no servidor não tem; o
``voice_id`` não precisa deles, e o servidor o usa. Carregar tudo aqui derrubava
o ``akemi-servidor`` na partida.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_WHERE = {
    "AudioCapture": ".capture",
    "FrameAssembler": ".process",
    "downmix_to_mono_int16": ".process",
    "resample_int16": ".process",
    "RingBuffer": ".ring_buffer",
    "VAD": ".vad",
    "FRAME_DURATION_MS": ".vad",
    "vad_frame_bytes": ".vad",
}

__all__ = list(_WHERE)


def __getattr__(name: str) -> Any:
    if name not in _WHERE:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_WHERE[name], __name__), name)
    globals()[name] = value
    return value
