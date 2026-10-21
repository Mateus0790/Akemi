"""Barramento de eventos assíncrono — o "sistema nervoso" da Akemi.

Todo módulo publica e consome :class:`Event` em vez de chamar outros módulos
diretamente. Isso permite que percepção, pensamento e ação rodem em paralelo
(ex.: monitorar o chat enquanto conversa por voz).

Cada evento carrega um :class:`Trust` que diz **de quem** ele veio. Essa
informação é usada pela política de ações: conteúdo vindo do público (chat
da live) nunca pode disparar ações no computador.
"""

from __future__ import annotations

import asyncio
import fnmatch
import itertools
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable

import structlog

logger = structlog.get_logger()


class Trust(IntEnum):
    """Quanto confiamos na origem de um evento (maior = mais confiável)."""

    PUBLIC = 0  # chat da live, Discord público, texto lido da tela
    SYSTEM = 1  # sensores internos, timers, a própria Akemi
    OWNER = 2  # o dono falando diretamente com ela (voz/console/API local)


class Priority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


_ids = itertools.count(1)


@dataclass(slots=True)
class Event:
    """Um acontecimento. ``kind`` usa notação com pontos, ex. ``chat.message``."""

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    trust: Trust = Trust.SYSTEM
    priority: Priority = Priority.NORMAL
    ts: float = field(default_factory=time.time)
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def text(self) -> str:
        return str(self.payload.get("text", ""))


Handler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    """Pub/sub assíncrono com padrões estilo glob (``chat.*``, ``*``)."""

    def __init__(self, max_queue: int = 1000) -> None:
        self._subs: list[tuple[str, Handler]] = []
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task | None = None
        self._inflight: set[asyncio.Task] = set()
        self.dropped = 0

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._subs.append((pattern, handler))

    def publish(self, event: Event) -> None:
        """Enfileira sem bloquear. Se a fila lotar, descarta o evento mais antigo
        de baixa prioridade — a Akemi prefere perder um "barulho" a travar."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover
                pass
            self._queue.put_nowait(event)
            logger.warning("event queue full, dropped oldest", kind=event.kind)

    async def dispatch(self, event: Event) -> None:
        for pattern, handler in list(self._subs):
            if fnmatch.fnmatchcase(event.kind, pattern):
                try:
                    result = handler(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("event handler failed", kind=event.kind)

    async def run(self) -> None:
        while True:
            event = await self._queue.get()
            # Cada evento é tratado numa task própria para que um handler lento
            # (ex.: chamada ao LLM) não segure o resto do sistema.
            task = asyncio.create_task(self.dispatch(event))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="event-bus")
        return self._task

    async def stop(self) -> None:
        for t in list(self._inflight):
            t.cancel()
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def drain(self, timeout: float = 2.0) -> None:
        """Espera a fila esvaziar (útil em testes)."""
        deadline = time.monotonic() + timeout
        while (not self._queue.empty() or self._inflight) and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
