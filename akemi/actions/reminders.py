"""Lembretes: "me lembra daqui a 10 minutos de tirar a pizza", "dia 5 de dezembro às 9h".

Com ``path`` (``data/lembretes.json``), os pendentes sobrevivem a um reinício.
Um lembrete que venceu com a Akemi desligada é dado assim que ela volta, avisando
que atrasou. A espera usa o relógio de parede, então o PC hibernar não perde o horário.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import structlog

from .registry import ActionRegistry
from .when import describe_when, parse_when

logger = structlog.get_logger()

MAX_DAYS = 366
MAX_PENDING = 200
CHECK_EVERY_S = 30.0


@dataclass
class Reminder:
    id: int
    text: str
    due_at: float
    task: asyncio.Task | None = field(default=None, repr=False)

    def describe(self, now: datetime | None = None) -> str:
        return f"{self.text} ({describe_when(datetime.fromtimestamp(self.due_at), now)})"


class Reminders:
    """Agenda avisos falados."""

    def __init__(
        self,
        on_fire: Callable[[str], None],
        max_minutes: float = MAX_DAYS * 24 * 60,
        *,
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.on_fire = on_fire
        self.max_minutes = max_minutes
        self.path = path
        self.clock = clock
        self._items: dict[int, Reminder] = {}
        self._next_id = 1

    @property
    def pending(self) -> list[Reminder]:
        return sorted(self._items.values(), key=lambda r: r.due_at)

    def add(self, minutes: float, text: str) -> Reminder:
        if not 0 < minutes <= self.max_minutes:
            raise ValueError(f"lembrete precisa ser entre 0 e {int(self.max_minutes)} minutos")
        return self._schedule(self.clock() + minutes * 60, text)

    def add_at(self, when: datetime | float, text: str) -> Reminder:
        due = when.timestamp() if isinstance(when, datetime) else float(when)
        now = self.clock()
        if due <= now:
            raise ValueError("esse horário já passou")
        if due - now > self.max_minutes * 60:
            raise ValueError(f"só consigo agendar até {self.max_minutes / 1440:g} dias para frente")
        return self._schedule(due, text)

    def _schedule(self, due_at: float, text: str, rid: int | None = None) -> Reminder:
        text = " ".join(str(text).split())[:300]
        if not text:
            raise ValueError("lembrar do quê?")
        if len(self._items) >= MAX_PENDING:
            raise ValueError("já tem lembrete demais pendente")
        if rid is None:
            rid, self._next_id = self._next_id, self._next_id + 1
        else:
            self._next_id = max(self._next_id, rid + 1)
        reminder = Reminder(rid, text, due_at)
        reminder.task = asyncio.get_running_loop().create_task(self._wait(reminder))
        self._items[rid] = reminder
        self._save()
        return reminder

    async def _wait(self, reminder: Reminder) -> None:
        try:
            while (left := reminder.due_at - self.clock()) > 0:
                await asyncio.sleep(min(left, CHECK_EVERY_S))
        except asyncio.CancelledError:
            return
        self._items.pop(reminder.id, None)
        self._save()
        logger.info("reminder fired", text=reminder.text)
        self.on_fire(reminder.text)

    def cancel(self, rid: int) -> Reminder | None:
        reminder = self._items.pop(rid, None)
        if reminder is not None:
            if reminder.task:
                reminder.task.cancel()
            self._save()
        return reminder

    def cancel_all(self) -> int:
        n = len(self._items)
        for r in self._items.values():
            if r.task:
                r.task.cancel()
        self._items.clear()
        self._save()
        return n

    # ------------------------------------------------------- persistência
    def _save(self) -> None:
        if self.path is None:
            return
        data = [{"id": r.id, "texto": r.text, "quando": r.due_at} for r in self.pending]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("could not save reminders", error=str(exc))

    def restore(self) -> int:
        """Reagenda os lembretes salvos (chamar com o loop rodando). Os vencidos saem na hora."""
        if self.path is None or not self.path.exists():
            return 0
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("reminders file unreadable", error=str(exc))
            return 0
        now, late, count = self.clock(), [], 0
        for row in rows if isinstance(rows, list) else []:
            try:
                rid, text, due = int(row["id"]), str(row["texto"]), float(row["quando"])
            except (KeyError, TypeError, ValueError):
                continue
            if rid in self._items:
                continue
            if due <= now:
                late.append((due, text))
                continue
            self._schedule(due, text, rid)
            count += 1
        self._save()
        for due, text in sorted(late):
            when = describe_when(datetime.fromtimestamp(due), datetime.fromtimestamp(now))
            self.on_fire(f"{text} (era para {when}, mas eu estava desligada)")
        return count + len(late)


def _minutes(value: float | str) -> float:
    try:
        return float(str(value).replace(",", "."))
    except ValueError as exc:
        raise ValueError("não entendi em quantos minutos") from exc


def register_reminder_actions(registry: ActionRegistry, reminders: Reminders) -> None:
    @registry.register(
        "lembrete",
        "agenda um lembrete que você fala em voz alta depois. Use minutos para 'daqui a X' "
        "ou quando para dia e hora ('amanhã às 9h', 'dia 5 de dezembro às 18h', 'sexta 10h')",
        example=('me lembra amanhã às 9h de ligar pro dentista',
                 '[[acao:lembrete {"quando": "amanhã às 9h", "texto": "ligar pro dentista"}]]'),
        params={"texto": "do que lembrar", "minutos": "daqui a quantos minutos (opcional)",
                "quando": "dia e hora (opcional)"},
    )
    def lembrete(texto: str, minutos: float | str | None = None, quando: str | None = None) -> str:
        if quando not in (None, ""):
            when = parse_when(str(quando), datetime.fromtimestamp(reminders.clock()))
            if when is None:
                if minutos in (None, ""):
                    raise ValueError(f"não entendi quando é '{quando}'")
            else:
                reminders.add_at(when, texto)
                return f"Combinado, te lembro {describe_when(when, datetime.fromtimestamp(reminders.clock()))}."
        if minutos in (None, ""):
            raise ValueError("faltou dizer quando")
        minutes = _minutes(minutos)
        reminders.add(minutes, texto)
        return f"Combinado, te lembro em {minutes:g} minuto{'s' if minutes != 1 else ''}."

    @registry.register("listar_lembretes", "diz quais lembretes estão pendentes", speak_result=True)
    def listar_lembretes() -> str:
        items = reminders.pending
        if not items:
            return "Você não tem nenhum lembrete pendente."
        now = datetime.fromtimestamp(reminders.clock())
        shown = "; ".join(r.describe(now) for r in items[:5])
        more = f" e mais {len(items) - 5}" if len(items) > 5 else ""
        return f"Você tem {len(items)} lembrete{'s' if len(items) > 1 else ''}: {shown}{more}."

    @registry.register("cancelar_lembretes", "cancela todos os lembretes pendentes")
    def cancelar_lembretes() -> str:
        return f"Cancelei {reminders.cancel_all()} lembrete(s)."
