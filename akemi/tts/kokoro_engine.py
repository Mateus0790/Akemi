"""Voz local com Kokoro (offline, tem voz feminina em pt-BR: ``pf_dora``).

Instalação::

    pip install -e ".[kokoro]"
    mkdir data\\voices\\kokoro
    curl -L -o data/voices/kokoro/kokoro-v1.0.int8.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx
    curl -L -o data/voices/kokoro/voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

Vozes pt-BR: ``pf_dora`` (feminina), ``pm_alex`` e ``pm_santa`` (masculinas).
A mente já manda uma frase por vez, então cada fala é sintetizada inteira e
tocada em blocos curtos que podem ser interrompidos.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from .base import TTSEngine
from .piper_engine import AudioPlayer, SoundDevicePlayer

logger = structlog.get_logger()


def float_to_pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


class KokoroTTS(TTSEngine):
    def __init__(
        self,
        model_path: Path | str,
        voices_path: Path | str,
        *,
        voice: str = "pf_dora",
        speed: float = 1.0,
        lang: str = "pt-br",
        volume: float = 1.0,
        player: AudioPlayer | None = None,
        kokoro: Any = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.voices_path = Path(voices_path)
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self.volume = volume
        self._kokoro = kokoro
        self._player = player
        self._cancel = threading.Event()
        self._lock = threading.Lock()  # uma fala por vez na placa de som

    def load(self) -> None:
        if self._kokoro is None:
            for path in (self.model_path, self.voices_path):
                if not path.exists():
                    raise FileNotFoundError(
                        f"Arquivo do Kokoro não encontrado: {path}. Veja como baixar em akemi/tts/kokoro_engine.py"
                    )
            try:
                from kokoro_onnx import Kokoro
            except ImportError as exc:  # pragma: no cover - depende do extra
                raise RuntimeError('Instale o extra de voz: pip install -e ".[kokoro]"') from exc
            self._kokoro = Kokoro(str(self.model_path), str(self.voices_path))
            if self.voice not in self._kokoro.get_voices():
                raise ValueError(f"Voz {self.voice!r} não existe no Kokoro")
            logger.info("kokoro voice loaded", voice=self.voice)
        if self._player is None:
            self._player = SoundDevicePlayer()

    async def warmup(self) -> None:
        await asyncio.to_thread(self.load)

    def _speak_sync(self, text: str) -> None:
        self.load()
        with self._lock:
            self._cancel.clear()
            samples, sample_rate = self._kokoro.create(text, voice=self.voice, speed=self.speed, lang=self.lang)
            if self._cancel.is_set():  # interrompida enquanto sintetizava
                return
            self._player.play(float_to_pcm16(np.asarray(samples) * self.volume), sample_rate, 1, self._cancel)

    async def speak(self, text: str) -> None:
        try:
            await asyncio.to_thread(self._speak_sync, text)
        except asyncio.CancelledError:
            self._cancel.set()
            raise

    async def stop(self) -> None:
        self._cancel.set()
