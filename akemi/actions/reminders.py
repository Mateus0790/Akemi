"""Lembretes: "me lembra daqui a 10 minutos de tirar a pizza do forno"."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import structlog

from .registry import ActionRegistry

logger = structlog.get_logger()


@dataclass
class Reminder:
    id: int
    text: str
    due_at: float
    task: asyncio.Task | None = field(default=None, repr=False)


class Reminders:
    """Agenda avisos falados. Ficam na memória: reiniciar a Akemi apaga os pendentes."""

    def __init__(self, on_fire: Callable[[str], None], max_minutes: float = 24 * 60) -> None:
        self.on_fire = on_fire
        self.max_minutes = max_minutes
        self._items: dict[int, Reminder] = {}
        self._next_id = 1

    @property
    def pending(self) -> list[Reminder]:
        return sorted(self._items.values(), key=lambda r: r.due_at)

    def add(self, minutes: float, text: str) -> Reminder:
        if not 0 < minutes <= self.max_minutes:
            raise ValueError(f"lembrete precisa ser entre 0 e {int(self.max_minutes)} minutos")
        rid, self._next_id = self._next_id, self._next_id + 1
        reminder = Reminder(rid, text.strip(), time.time() + minutes * 60)
        reminder.task = asyncio.get_running_loop().create_task(self._wait(reminder, minutes * 60))
        self._items[rid] = reminder
        return reminder

    async def _wait(self, reminder: Reminder, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._items.pop(reminder.id, None)
        logger.info("reminder fired", text=reminder.text)
        self.on_fire(reminder.text)

    def cancel_all(self) -> int:
        n = len(self._items)
        for r in self._items.values():
            if r.task:
                r.task.cancel()
        self._items.clear()
        return n


def register_reminder_actions(registry: ActionRegistry, reminders: Reminders) -> None:
    @registry.register("lembrete", "agenda um lembrete que você fala em voz alta depois",
                       example=('me lembra em 10 minutos de tirar a pizza', '[[acao:lembrete {"minutos": 10, "texto": "tirar a pizza do forno"}]]'),
                       params={"minutos": "daqui a quantos minutos", "texto": "do que lembrar"})
    def lembrete(minutos: float | str, texto: str) -> str:
        try:
            minutes = float(str(minutos).replace(",", "."))
        except ValueError as exc:
            raise ValueError("não entendi em quantos minutos") from exc
        r = reminders.add(minutes, texto)
        return f"Combinado, te lembro em {minutes:g} minuto{'s' if minutes != 1 else ''}."

    @registry.register("cancelar_lembretes", "cancela todos os lembretes pendentes")
    def cancelar_lembretes() -> str:
        return f"Cancelei {reminders.cancel_all()} lembrete(s)."
