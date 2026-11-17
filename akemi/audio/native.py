"""Escolhe entre o núcleo de áudio em Rust e o em Python.

O módulo ``akemi_audio_rs`` (``rust-core/akemi-audio``) faz o mesmo que
``process.py`` e o ``webrtcvad``, com o mesmo resultado byte a byte. Se ele
estiver instalado, a captura e o VAD usam ele; se não, tudo continua em Python
como sempre foi. Nada mais na Akemi precisa saber qual dos dois está rodando.

Para forçar o Python mesmo com o Rust instalado (ex.: para comparar), rode com
``AKEMI_RUST=0`` no ambiente. Veja docs/RUST.md.
"""

from __future__ import annotations

import os
from types import ModuleType
from typing import Any

import structlog

logger = structlog.get_logger()


def _load() -> ModuleType | None:
    if os.environ.get("AKEMI_RUST", "1").strip().lower() in ("0", "false", "nao", "não", "off"):
        return None
    try:
        import akemi_audio_rs
    except ImportError:
        return None
    return akemi_audio_rs


rust: ModuleType | None = _load()
BACKEND = "rust" if rust is not None else "python"


def make_pipeline(device_rate: int, channels: int, output_rate: int = 16000, frame_ms: int = 20) -> Any:
    """Pipeline de um callback: ``push(bytes) -> list[bytes]`` e ``clear()``."""
    if rust is not None:
        return rust.AudioPipeline(device_rate, channels, output_rate, frame_ms)
    from .process import AudioPipeline

    return AudioPipeline(device_rate, channels, output_rate, frame_ms)


def make_vad_engine(aggressiveness: int, sample_rate: int = 16000) -> Any:
    """Motor do WebRTC VAD com ``is_speech(frame, sample_rate)``."""
    if rust is not None:
        return rust.Vad(aggressiveness, sample_rate)
    import webrtcvad

    return webrtcvad.Vad(aggressiveness)


def describe() -> dict[str, str]:
    """Para logs e diagnóstico: qual núcleo está rodando."""
    if rust is None:
        return {"backend": "python"}
    return {"backend": "rust", "version": getattr(rust, "__version__", "?")}
