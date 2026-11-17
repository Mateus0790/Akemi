"""Saída de voz: fila de falas, moderação final e interrupção.

O :class:`Speaker` é o *único* caminho para a Akemi falar. Por isso a
moderação de saída roda aqui: nada vira voz sem passar por ela.
"""

from __future__ import annotations

import abc
import asyncio
import time
from collections import deque
from typing import Callable

import structlog

from akemi.core.events import Event, EventBus, Trust
from akemi.safety.moderation import Moderator

logger = structlog.get_logger()


class TTSEngine(abc.ABC):
    @abc.abstractmethod
    async def speak(self, text: str) -> None:
        """Fala o texto. Deve ser cancelável (asyncio.CancelledError)."""

    async def stop(self) -> None:  # pragma: no cover - default no-op
        return None


class ConsoleTTS(TTSEngine):
    """Imprime em vez de falar. Útil para desenvolver sem áudio."""

    def __init__(self, printer: Callable[[str], None] = print, chars_per_second: float = 0.0) -> None:
        self.printer = printer
        self.cps = chars_per_second

    async def speak(self, text: str) -> None:
        self.printer(f"🗣️  Akemi: {text}")
        if self.cps > 0:
            await asyncio.sleep(len(text) / self.cps)


class Speaker:
    def __init__(self, bus: EventBus, engine: TTSEngine, moderator: Moderator) -> None:
        self.bus = bus
        self.engine = engine
        self.moderator = moderator
        self._queue: asyncio.Queue[tuple[str, Trust, dict | None]] = asyncio.Queue()
        self._timed_origin: object = None
        self._recent: deque[tuple[float, str]] = deque(maxlen=10)
        self._current: asyncio.Task | None = None
        self._task: asyncio.Task | None = None
        self.spoken: list[str] = []

    def register(self) -> None:
        self.bus.subscribe("speech.say", self.on_say)
        self.bus.subscribe("speech.stop", self.on_stop)
        self.bus.subscribe("owner.speech_started", self.on_stop)

    async def on_say(self, event: Event) -> None:
        verdict = self.moderator.check_outgoing(event.text)
        if not verdict.allowed:
            logger.warning("speech blocked by moderation", reasons=verdict.reasons, origin=event.payload.get("origin"))
            return
        if verdict.reasons:
            logger.info("speech sanitized", reasons=verdict.reasons)
        latency = event.payload.get("latency")
        if latency is not None:
            latency = {**latency, "origin_id": event.payload.get("origin_id")}
        await self._queue.put((verdict.text, event.trust, latency))

    async def on_stop(self, event: Event | None = None) -> None:
        """Interrompe a fala atual e descarta a fila (o dono começou a falar)."""
        while not self._queue.empty():
            self._queue.get_nowait()
        if self._current and not self._current.done():
            self._current.cancel()
        await self.engine.stop()

    async def run(self) -> None:
        while True:
            text, trust, latency = await self._queue.get()
            if latency is not None:
                self._log_latency(latency)
            # speech.started/finished servem para o avatar (boca, expressão)
            # e para a Akemi não "ouvir a si mesma" no áudio do sistema/mic.
            self._recent.append((time.time(), text))
            self.bus.publish(Event("speech.started", {"text": text, "origin_trust": int(trust)}, source="speaker"))
            self._current = asyncio.create_task(self.engine.speak(text))
            status = "done"
            try:
                await self._current
                self.spoken.append(text)
            except asyncio.CancelledError:
                if self._task is not None and self._task.cancelling():
                    raise
                status = "interrupted"
                logger.info("speech interrupted")
            except Exception:
                status = "error"
                logger.exception("tts failed")
            self._recent.append((time.time(), text))  # marca o fim (janela de eco)
            self.bus.publish(
                Event(
                    "speech.finished",
                    {"text": text, "status": status, "origin_trust": int(trust)},
                    source="speaker",
                )
            )

    def _log_latency(self, latency: dict) -> None:
        """Uma linha por resposta à voz: onde foi parar o tempo entre você
        parar de falar e ela começar a responder (docs/DESEMPENHO.md)."""
        origin = latency.get("origin_id")
        if origin is not None and origin == self._timed_origin:
            return  # só a primeira frase de cada resposta
        self._timed_origin = origin
        silence = float(latency.get("end_silence_ms", 0))
        since_heard = (time.monotonic() - float(latency["heard_at"])) * 1000
        parts = {k: float(latency.get(k, 0)) for k in ("stt_queue_ms", "stt_ms", "voice_id_ms")}
        brain = max(since_heard - sum(parts.values()), 0.0)
        logger.info(
            "voice reply latency",
            total_ms=round(silence + since_heard),
            end_silence_ms=round(silence),
            **{k: round(v) for k, v in parts.items()},
            brain_ms=round(brain),
        )

    @property
    def recent_speech(self) -> list[tuple[float, str]]:
        return list(self._recent)

    @property
    def speaking(self) -> bool:
        return self._current is not None and not self._current.done()

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="speaker")
        return self._task

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
