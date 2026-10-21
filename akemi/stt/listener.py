"""Transforma frames de áudio em eventos.

- :class:`OwnerVoiceInput` — microfone do dono → ``owner.speech_started`` (para
  interromper a Akemi) e ``owner.message`` (Trust.OWNER).
- :class:`AmbientAudioInput` — áudio do sistema → ``ambient.audio`` (Trust.PUBLIC).

As duas classes não sabem de onde vêm os frames; recebem 20 ms de PCM16 mono
16 kHz por ``feed``. Isso deixa toda a lógica testável sem placa de som.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

import numpy as np
import structlog

from akemi.core.events import Event, EventBus, Priority, Trust
from akemi.core.pause import PauseSwitch

from .filters import contains_wake_word, is_echo, is_hallucination
from .transcriber import SpeechSegmenter

logger = structlog.get_logger()

Transcribe = Callable[[bytes], Awaitable[str]]
MicMode = Literal["wake_word", "always"]


class SpeechState(Protocol):
    """O que o listener precisa saber sobre a fala da própria Akemi."""

    @property
    def speaking(self) -> bool: ...

    @property
    def recent_speech(self) -> list[tuple[float, str]]: ...


def rms(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0


class OwnerVoiceInput:
    def __init__(
        self,
        bus: EventBus,
        transcribe: Transcribe,
        is_speech: Callable[[bytes], bool],
        speech_state: SpeechState,
        pause: PauseSwitch | None = None,
        *,
        mode: MicMode = "wake_word",
        wake_words: list[str] | None = None,
        followup_window_s: float = 20.0,
        barge_in_rms: float = 1500.0,
        end_silence_ms: int = 700,
    ) -> None:
        self.bus = bus
        self.transcribe = transcribe
        self.vad = is_speech
        self.speech_state = speech_state
        self.pause = pause
        self.mode = mode
        self.wake_words = wake_words or ["akemi"]
        self.followup_window_s = followup_window_s
        self.barge_in_rms = barge_in_rms
        self.segmenter = SpeechSegmenter(self._is_speech, end_silence_ms=end_silence_ms)
        self.last_exchange_at = 0.0
        self.dropped: dict[str, int] = {}

    def _is_speech(self, frame: bytes) -> bool:
        if not self.vad(frame):
            return False
        # Enquanto ela fala, a caixa de som vaza no mic. Exige voz mais forte
        # (alguém falando perto do mic) para contar como interrupção.
        if self.speech_state.speaking:
            return rms(frame) >= self.barge_in_rms
        return True

    def _drop(self, reason: str, text: str = "") -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1
        logger.debug("mic transcript dropped", reason=reason, text=text[:80])

    def mark_exchange(self, ts: float | None = None) -> None:
        """Chamado quando a Akemi responde: abre a janela de continuação sem
        precisar repetir o nome dela."""
        self.last_exchange_at = ts or time.time()

    def addressed(self, text: str, now: float) -> bool:
        if self.mode == "always":
            return True
        if contains_wake_word(text, self.wake_words):
            return True
        return now - self.last_exchange_at <= self.followup_window_s

    async def feed(self, frame: bytes) -> str | None:
        """Processa um frame. Retorna o texto publicado, se houver."""
        was_in_speech = self.segmenter.in_speech
        segment = self.segmenter.push(frame)
        if not was_in_speech and self.segmenter.in_speech and self.speech_state.speaking:
            # Barge-in: você começou a falar por cima dela.
            self.bus.publish(
                Event("owner.speech_started", source="mic", trust=Trust.OWNER, priority=Priority.URGENT)
            )
        if segment is None:
            return None
        return await self.handle_segment(segment)

    async def handle_segment(self, segment: bytes, now: float | None = None) -> str | None:
        try:
            text = (await self.transcribe(segment)).strip()
        except Exception as exc:
            logger.error("mic transcription failed", error=str(exc))
            return None
        now = now or time.time()
        if is_hallucination(text):
            self._drop("hallucination", text)
            return None
        if is_echo(text, self.speech_state.recent_speech, now=now):
            self._drop("echo", text)
            return None
        if not self.addressed(text, now):
            # Você falando com outra pessoa: não é para ela, e não guardamos.
            self._drop("not_addressed", text)
            return None
        self.last_exchange_at = now
        logger.info("owner said", text=text)
        self.bus.publish(
            Event("owner.speech_started", source="mic", trust=Trust.OWNER, priority=Priority.URGENT)
        )
        self.bus.publish(
            Event("owner.message", {"text": text, "via": "mic"}, source="mic",
                  trust=Trust.OWNER, priority=Priority.HIGH)
        )
        return text


class AmbientAudioInput:
    def __init__(
        self,
        bus: EventBus,
        transcribe: Transcribe,
        is_speech: Callable[[bytes], bool],
        speech_state: SpeechState,
        pause: PauseSwitch | None = None,
    ) -> None:
        self.bus = bus
        self.transcribe = transcribe
        self.speech_state = speech_state
        self.pause = pause
        self.segmenter = SpeechSegmenter(is_speech)

    async def feed(self, frame: bytes) -> str | None:
        if (self.pause and self.pause.paused) or self.speech_state.speaking:
            return None  # não se ouve no loopback
        segment = self.segmenter.push(frame)
        if segment is None:
            return None
        try:
            text = (await self.transcribe(segment)).strip()
        except Exception as exc:
            logger.error("ambient transcription failed", error=str(exc))
            return None
        if len(text) <= 3 or is_hallucination(text) or is_echo(text, self.speech_state.recent_speech):
            return None
        self.bus.publish(
            Event("ambient.audio", {"text": text}, source="system-audio",
                  trust=Trust.PUBLIC, priority=Priority.LOW)
        )
        return text
