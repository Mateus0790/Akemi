"""Transforma frames de áudio em eventos.

- :class:`OwnerVoiceInput` — microfone do dono → ``owner.speech_started`` (para
  interromper a Akemi) e ``owner.message`` (Trust.OWNER).
- :class:`AmbientAudioInput` — áudio do sistema → ``ambient.audio`` (Trust.PUBLIC).

As duas classes não sabem de onde vêm os frames; recebem 20 ms de PCM16 mono
16 kHz por ``feed``. Isso deixa toda a lógica testável sem placa de som.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

import numpy as np
import structlog

from akemi.core.events import Event, EventBus, Priority, Trust
from akemi.core.pause import PauseSwitch

from akemi.audio.voice_id import VoiceID

from .filters import contains_wake_word, is_bare_wake_word, is_echo, is_hallucination
from .transcriber import SpeechSegmenter

logger = structlog.get_logger()

Transcribe = Callable[[bytes], Awaitable[str]]
# Áudio do PC: ``None`` = o Whisper estava ocupado e o trecho foi descartado.
BackgroundTranscribe = Callable[[bytes], Awaitable[str | None]]
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
        min_avg_logprob: float = -0.8,
        max_no_speech_prob: float = 0.5,
        voice_id: VoiceID | None = None,
    ) -> None:
        self.bus = bus
        # Com a voz cadastrada, cada frase é conferida antes de valer como do dono.
        self.voice_id = voice_id
        # Sem o nome dela, só aceita o que o Whisper ouviu com segurança;
        # e nunca aceita o que ele acha que nem é fala.
        self.min_avg_logprob = min_avg_logprob
        self.max_no_speech_prob = max_no_speech_prob
        self.transcribe = transcribe
        self.vad = is_speech
        self.speech_state = speech_state
        self.pause = pause
        self.mode = mode
        self.wake_words = wake_words or ["akemi"]
        self.followup_window_s = followup_window_s
        self.barge_in_rms = barge_in_rms
        self.end_silence_ms = end_silence_ms
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

    def _low_confidence(self, transcript: str) -> bool:
        return getattr(transcript, "avg_logprob", 0.0) < self.min_avg_logprob

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
        return await self.handle_segment(segment, heard=time.monotonic())

    def _latency(self, heard: float, raw: str, voice_s: float = 0.0) -> dict[str, float]:
        """Partes do tempo de resposta até aqui (o Speaker completa e loga)."""
        stt_s = time.monotonic() - heard - voice_s
        queued = float(getattr(raw, "queued_s", 0.0))
        return {
            "heard_at": heard,  # time.monotonic() quando o fim da fala foi detectado
            "end_silence_ms": float(self.end_silence_ms),
            "stt_queue_ms": round(queued * 1000),
            "stt_ms": round(max(stt_s - queued, 0.0) * 1000),
            "voice_id_ms": round(voice_s * 1000),
        }

    async def handle_segment(
        self, segment: bytes, now: float | None = None, heard: float | None = None
    ) -> str | None:
        heard = time.monotonic() if heard is None else heard
        try:
            raw = await self.transcribe(segment)
        except Exception as exc:
            logger.error("mic transcription failed", error=str(exc))
            return None
        text = raw.strip()
        now = now or time.time()
        if is_hallucination(text):
            self._drop("hallucination", text)
            return None
        if is_echo(text, self.speech_state.recent_speech, now=now):
            self._drop("echo", text)
            return None
        if getattr(raw, "no_speech_prob", 0.0) > self.max_no_speech_prob:
            # Chiado/ruído. O Whisper tende a "ouvir" o nome dela (vem na dica
            # de vocabulário), então isso vale mesmo quando o texto é "Akemi".
            self._drop("no_speech", text)
            return None
        called = self.mode == "wake_word" and contains_wake_word(text, self.wake_words)
        if not self.addressed(text, now):
            # Você falando com outra pessoa: não é para ela, e não guardamos.
            self._drop("not_addressed", text)
            return None
        if not called and self._low_confidence(raw):
            # Ruído ou a voz dela vazando, transcritos como frase sem sentido.
            self._drop("low_confidence", text)
            return None
        self.last_exchange_at = now
        if called and is_bare_wake_word(text, self.wake_words):
            # Só o chamado: para de falar (se estiver) e espera o pedido.
            logger.info("wake word heard, listening")
            self.bus.publish(
                Event("owner.speech_started", source="mic", trust=Trust.OWNER, priority=Priority.URGENT)
            )
            self.bus.publish(Event("owner.wake", source="mic", trust=Trust.OWNER, priority=Priority.HIGH))
            return None
        voice_s = 0.0
        if self.voice_id is not None:
            started = time.monotonic()
            verdict = await asyncio.to_thread(self.voice_id.verify, segment, now)
            voice_s = time.monotonic() - started
            if verdict.who != "dono":
                # Não é você (ou não deu para ter certeza): vira conversa de visita,
                # sem confiança de dono. Interromper ela, qualquer um pode.
                logger.info("voice not recognized", who=verdict.who, text=text)
                self.bus.publish(
                    Event("owner.speech_started", source="mic", trust=Trust.PUBLIC, priority=Priority.URGENT)
                )
                self.bus.publish(Event(
                    "voice.guest",
                    {"text": text, "quem": verdict.who, "nota": verdict.score, "motivo": verdict.reason,
                     "latency": self._latency(heard, raw, voice_s)},
                    source="mic", trust=Trust.PUBLIC, priority=Priority.HIGH,
                ))
                return text
        logger.info("owner said", text=text)
        self.bus.publish(
            Event("owner.speech_started", source="mic", trust=Trust.OWNER, priority=Priority.URGENT)
        )
        self.bus.publish(
            Event("owner.message", {"text": text, "via": "mic", "latency": self._latency(heard, raw, voice_s)},
                  source="mic", trust=Trust.OWNER, priority=Priority.HIGH)
        )
        return text


class AmbientAudioInput:
    """Áudio do PC → ``ambient.audio``, sem atrapalhar você.

    - Enquanto você fala no microfone (``owner_talking``) ou o Whisper está
      ocupado, o trecho do PC é descartado em vez de fazer fila.
    - ``max_load`` limita a fração do tempo que o Whisper passa com o áudio do
      PC (um "balde" que enche ``max_load`` segundos por segundo, até
      ``burst_s``). Num PC rápido nada se perde; num lento, o PC não trava.
    """

    def __init__(
        self,
        bus: EventBus,
        transcribe: BackgroundTranscribe,
        is_speech: Callable[[bytes], bool],
        speech_state: SpeechState,
        pause: PauseSwitch | None = None,
        *,
        owner_talking: Callable[[], bool] | None = None,
        max_load: float = 0.25,
        burst_s: float = 4.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bus = bus
        self.transcribe = transcribe
        self.speech_state = speech_state
        self.pause = pause
        self.segmenter = SpeechSegmenter(is_speech)
        self.owner_talking = owner_talking
        self.max_load = max_load
        self.burst_s = burst_s
        self.clock = clock
        self._budget = burst_s
        self._refilled_at = clock()
        self.dropped: dict[str, int] = {}

    def _drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    def _refill(self) -> float:
        now = self.clock()
        if self.max_load < 1.0:
            self._budget = min(self.burst_s, self._budget + (now - self._refilled_at) * self.max_load)
        self._refilled_at = now
        return now

    async def feed(self, frame: bytes) -> str | None:
        if (self.pause and self.pause.paused) or self.speech_state.speaking:
            return None  # não se ouve no loopback
        segment = self.segmenter.push(frame)
        if segment is None:
            return None
        if self.owner_talking is not None and self.owner_talking():
            self._drop("owner_talking")  # a sua fala vai precisar do Whisper já já
            return None
        started = self._refill()
        if self.max_load < 1.0 and self._budget <= 0:
            self._drop("cpu_budget")
            return None
        try:
            raw = await self.transcribe(segment)
        except Exception as exc:
            logger.error("ambient transcription failed", error=str(exc))
            return None
        if raw is None:
            self._drop("whisper_busy")
            return None
        if self.max_load < 1.0:
            self._refill()
            self._budget -= float(getattr(raw, "took_s", 0.0) or (self.clock() - started))
        text = raw.strip()
        if len(text) <= 3 or is_hallucination(text) or is_echo(text, self.speech_state.recent_speech):
            return None
        self.bus.publish(
            Event("ambient.audio", {"text": text}, source="system-audio",
                  trust=Trust.PUBLIC, priority=Priority.LOW)
        )
        return text
