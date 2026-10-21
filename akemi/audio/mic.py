"""Captura do microfone via ``sounddevice`` → frames PCM16 mono 16 kHz de 20 ms.

Abre o dispositivo na taxa nativa dele (WASAPI costuma recusar 16 kHz) e
reaproveita o mesmo resample/fatiamento do loopback.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import structlog

from .process import FrameAssembler, StreamResampler, downmix_to_mono_int16
from .vad import vad_frame_bytes

logger = structlog.get_logger()

TARGET_RATE = 16000


class MicCapture:
    def __init__(self, device: str | int | None = None, block_ms: int = 20) -> None:
        self.device = device
        self.block_ms = block_ms
        self._stream = None
        self._callback: Callable[[bytes], None] | None = None
        self._resampler: StreamResampler | None = None
        self._frames = FrameAssembler(vad_frame_bytes(TARGET_RATE, 20))
        self._channels = 1

    @staticmethod
    def list_devices() -> list[str]:
        import sounddevice as sd

        return [
            f"{i}: {d['name']} ({d['max_input_channels']} canais)"
            for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
        ]

    def _on_audio(self, indata, frames, time_info, status) -> None:  # thread do PortAudio
        if status:
            logger.debug("mic status", status=str(status))
        try:
            mono = downmix_to_mono_int16(bytes(indata), self._channels)
            out = self._resampler.push(mono) if self._resampler else mono
            for frame in self._frames.push(np.asarray(out, dtype=np.int16).tobytes()):
                if self._callback:
                    self._callback(frame)
        except Exception as exc:
            logger.error("mic callback failed", error=str(exc))

    def start(self, callback: Callable[[bytes], None]) -> None:
        try:
            import sounddevice as sd
        except (ImportError, OSError) as exc:
            raise RuntimeError('Instale o extra do microfone: pip install -e ".[mic]"') from exc
        info = sd.query_devices(self.device, "input")
        rate = int(info["default_samplerate"])
        self._channels = 1 if info["max_input_channels"] >= 1 else 0
        self._resampler = StreamResampler(rate, TARGET_RATE) if rate != TARGET_RATE else None
        self._callback = callback
        self._stream = sd.RawInputStream(
            samplerate=rate,
            channels=self._channels,
            dtype="int16",
            device=self.device,
            blocksize=rate * self.block_ms // 1000,
            callback=self._on_audio,
        )
        self._stream.start()
        logger.info("mic capture started", device=info["name"], sample_rate=rate)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.warning("error stopping mic", error=str(exc))
            self._stream = None
        self._frames.clear()
