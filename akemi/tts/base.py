"""Saída de voz: fila de falas, moderação final e interrupção.

O :class:`Speaker` é o *único* caminho para a Akemi falar. Por isso a
moderação de saída roda aqui: nada vira voz sem passar por ela.
"""

from __future__ import annotations

import abc
import asyncio
from typing import Callable

import structlog

from akemi.core.events import Event, EventBus
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
        self._queue: asyncio.Queue[str] = asyncio.Queue()
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
        await self._queue.put(verdict.text)

    async def on_stop(self, event: Event | None = None) -> None:
        """Interrompe a fala atual e descarta a fila (o dono começou a falar)."""
        while not self._queue.empty():
            self._queue.get_nowait()
        if self._current and not self._current.done():
            self._current.cancel()
        await self.engine.stop()

    async def run(self) -> None:
        while True:
            text = await self._queue.get()
            self._current = asyncio.create_task(self.engine.speak(text))
            try:
                await self._current
                self.spoken.append(text)
            except asyncio.CancelledError:
                if self._task is not None and self._task.cancelling():
                    raise
                logger.info("speech interrupted")
            except Exception:
                logger.exception("tts failed")

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
