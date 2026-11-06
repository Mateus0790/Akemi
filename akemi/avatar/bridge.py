"""Liga o barramento de eventos da Akemi ao avatar.

O avatar reage ao que já passa no barramento (``speech.started``/``finished``,
``owner.*``, ``casca.*``) e lê o humor dela uma vez por segundo. Carinho na
cabeça volta como o evento ``avatar.carinho``, que só o mouse do dono gera.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from akemi.core.events import Event, EventBus, Priority, Trust

FORWARD = ("speech.started", "speech.finished", "owner.speech_started", "owner.message", "casca.*")


class AvatarBridge:
    def __init__(
        self,
        bus: EventBus,
        window,
        *,
        mood: Callable[[], tuple[str, float]] | None = None,
        on_menu: Callable[[str], None] | None = None,
    ) -> None:
        self.bus = bus
        self.window = window
        self.loop: asyncio.AbstractEventLoop | None = None
        if mood is not None:
            window.mood = mood
        window.on_pat = self._pat_from_tk
        if on_menu is not None:
            window.on_menu = lambda what: self._call_soon(on_menu, what)

    def register(self) -> None:
        self.loop = asyncio.get_running_loop()
        for pattern in FORWARD:
            self.bus.subscribe(pattern, self._forward)

    def _forward(self, event: Event) -> None:
        payload = {"text": event.text} if event.text else {}
        self.window.post(event.kind, payload)

    def _call_soon(self, fn, *args) -> None:
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(fn, *args)

    def _pat_from_tk(self) -> None:
        """Chamado na thread do Tk: o carinho vira evento no barramento."""
        self._call_soon(self.bus.publish, Event(
            "avatar.carinho", {}, source="avatar", trust=Trust.SYSTEM, priority=Priority.LOW,
        ))
