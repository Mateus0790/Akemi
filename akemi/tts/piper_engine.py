"""Voz local com Piper (rápido, offline, grátis, tem vozes pt-BR).

Instalação::

    pip install -e ".[tts]"
    python -m piper.download_voices pt_BR-faber-medium --data-dir data/voices

A síntese e a reprodução rodam numa thread, em blocos curtos, checando um
sinal de cancelamento entre eles — assim a Akemi para de falar quase na hora
quando o dono começa a falar.
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading
from pathlib import Path
from typing import Any, Iterable, Protocol

import structlog

from .base import TTSEngine

logger = structlog.get_logger()


class AudioPlayer(Protocol):
    def play(self, pcm16: bytes, sample_rate: int, channels: int, cancel: threading.Event) -> None:
        """Toca PCM16 bloqueando, mas retorna cedo se ``cancel`` for setado."""


class SoundDevicePlayer:
    """Saída de áudio via ``sounddevice`` (PortAudio)."""

    def __init__(self, device: str | int | None = None, block_ms: int = 50) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:  # pragma: no cover - depende do extra
            raise RuntimeError('Instale o extra de voz: pip install -e ".[tts]"') from exc
        self._sd = sd
        self.device = device
        self.block_ms = block_ms
        self._stream = None
        self._format: tuple[int, int] | None = None

    def _ensure_stream(self, sample_rate: int, channels: int):
        if self._stream is None or self._format != (sample_rate, channels):
            self.close()
            self._stream = self._sd.RawOutputStream(
                samplerate=sample_rate, channels=channels, dtype="int16", device=self.device
            )
            self._stream.start()
            self._format = (sample_rate, channels)
        return self._stream

    def play(self, pcm16: bytes, sample_rate: int, channels: int, cancel: threading.Event) -> None:
        stream = self._ensure_stream(sample_rate, channels)
        # Em quadros inteiros: 22050 Hz × 50 ms daria 2205 bytes, que corta uma
        # amostra int16 no meio e o PortAudio recusa.
        frames = max(1, sample_rate * self.block_ms // 1000)
        step = frames * channels * 2
        for i in range(0, len(pcm16), step):
            if cancel.is_set():
                stream.abort()  # descarta o que está no buffer da placa
                stream.start()
                return
            stream.write(pcm16[i : i + step])

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # pragma: no cover
                pass
            self._stream = None
            self._format = None


def _make_syn_config(**kwargs: Any):
    """Cria SynthesisConfig só com os campos que a versão instalada aceita."""
    from piper import SynthesisConfig

    names = {f.name for f in dataclasses.fields(SynthesisConfig)}
    return SynthesisConfig(**{k: v for k, v in kwargs.items() if k in names and v is not None})


class PiperTTS(TTSEngine):
    def __init__(
        self,
        model_path: Path | str,
        *,
        speaker_id: int | None = None,
        length_scale: float = 1.0,
        volume: float = 1.0,
        player: AudioPlayer | None = None,
        voice: Any = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.speaker_id = speaker_id
        self.length_scale = length_scale
        self.volume = volume
        self._voice = voice
        self._player = player
        self._cancel = threading.Event()
        self._lock = threading.Lock()  # uma fala por vez na placa de som
        self._syn_config = None

    # ------------------------------------------------------------ carga
    def load(self) -> None:
        """Carrega modelo e saída de áudio. Chame antes para não atrasar a 1ª fala."""
        if self._voice is None:
            if not self.model_path.exists():
                raise FileNotFoundError(
                    f"Voz não encontrada em {self.model_path}. Baixe com: "
                    f"python -m piper.download_voices {self.model_path.stem} "
                    f"--data-dir {self.model_path.parent}"
                )
            try:
                from piper import PiperVoice
            except ImportError as exc:  # pragma: no cover - depende do extra
                raise RuntimeError('Instale o extra de voz: pip install -e ".[tts]"') from exc
            self._voice = PiperVoice.load(self.model_path)
            logger.info("piper voice loaded", model=self.model_path.name)
        if self._syn_config is None:
            try:
                self._syn_config = _make_syn_config(
                    speaker_id=self.speaker_id, length_scale=self.length_scale, volume=self.volume
                )
            except ImportError:
                self._syn_config = None
        if self._player is None:
            self._player = SoundDevicePlayer()

    async def warmup(self) -> None:
        await asyncio.to_thread(self.load)

    # ------------------------------------------------------------- fala
    def _chunks(self, text: str) -> Iterable[Any]:
        if self._syn_config is not None:
            return self._voice.synthesize(text, syn_config=self._syn_config)
        return self._voice.synthesize(text)

    def _speak_sync(self, text: str) -> None:
        self.load()
        with self._lock:
            self._cancel.clear()
            for chunk in self._chunks(text):
                if self._cancel.is_set():
                    return
                self._player.play(
                    chunk.audio_int16_bytes,
                    chunk.sample_rate,
                    getattr(chunk, "sample_channels", 1),
                    self._cancel,
                )

    async def speak(self, text: str) -> None:
        try:
            await asyncio.to_thread(self._speak_sync, text)
        except asyncio.CancelledError:
            self._cancel.set()
            raise

    async def stop(self) -> None:
        self._cancel.set()
