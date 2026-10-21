"""Fala → texto.

- :class:`SpeechSegmenter`: junta frames de 20 ms em trechos de fala usando o
  VAD (com folga antes/depois para não cortar palavras). Puro, testável.
- :class:`WhisperTranscriber`: faster-whisper rodando numa thread, para não
  travar o loop de eventos. Dependência opcional (extra ``stt``).
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Callable

import numpy as np
import structlog

logger = structlog.get_logger()

FRAME_MS = 20


class SpeechSegmenter:
    def __init__(
        self,
        is_speech: Callable[[bytes], bool],
        *,
        start_frames: int = 6,  # 120 ms de fala para começar
        end_silence_ms: int = 700,
        preroll_ms: int = 300,
        max_segment_s: float = 15.0,
    ) -> None:
        self.is_speech = is_speech
        self.start_frames = start_frames
        self.end_silence_frames = end_silence_ms // FRAME_MS
        self.max_frames = int(max_segment_s * 1000 / FRAME_MS)
        self._preroll: deque[bytes] = deque(maxlen=preroll_ms // FRAME_MS)
        self._voiced_run = 0
        self._silence_run = 0
        self._segment: list[bytes] | None = None

    @property
    def in_speech(self) -> bool:
        return self._segment is not None

    def push(self, frame: bytes) -> bytes | None:
        """Recebe um frame; devolve um segmento completo (PCM16) quando a fala termina."""
        speech = self.is_speech(frame)
        if self._segment is None:
            self._preroll.append(frame)
            self._voiced_run = self._voiced_run + 1 if speech else 0
            if self._voiced_run >= self.start_frames:
                self._segment = list(self._preroll)
                self._preroll.clear()
                self._silence_run = 0
            return None

        self._segment.append(frame)
        self._silence_run = 0 if speech else self._silence_run + 1
        if self._silence_run >= self.end_silence_frames or len(self._segment) >= self.max_frames:
            seg = b"".join(self._segment)
            self._segment = None
            self._voiced_run = 0
            return seg
        return None


class WhisperTranscriber:
    def __init__(self, model_size: str = "small", language: str = "pt", device: str = "auto") -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depende do extra
            raise RuntimeError('Instale o extra de STT: pip install -e ".[stt]"') from exc
        compute = "int8"
        self.language = language
        self._model = WhisperModel(model_size, device=device, compute_type=compute)
        logger.info("whisper loaded", model=model_size)

    def _transcribe_sync(self, pcm16: bytes) -> str:
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(audio, language=self.language, vad_filter=False, beam_size=1)
        return " ".join(s.text.strip() for s in segments).strip()

    async def transcribe(self, pcm16: bytes) -> str:
        return await asyncio.to_thread(self._transcribe_sync, pcm16)
