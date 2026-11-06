"""Lembretes: "me lembra daqui a 10 minutos de tirar a pizza", "dia 5 de dezembro às 9h".

Com ``path`` (``data/lembretes.json``), os pendentes sobrevivem a um reinício.
Um lembrete que venceu com a Akemi desligada é dado assim que ela volta, avisando
que atrasou. A espera usa o relógio de parede, então o PC hibernar não perde o horário.

Um pedido com horário ("me lembra amanhã às 9 de ligar pro mecânico", "timer de
5 minutos") é entendido direto da frase, sem o modelo (``Reminders.direct_request``):
funciona igual com o cérebro reserva local. Logo depois de tocar, "adia 10
minutos" (ou "soneca") marca de novo.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import structlog

from .registry import ActionCall, ActionRegistry
from .when import NUM, UNIT, describe_when, find_when, fold, number, parse_duration, parse_when

logger = structlog.get_logger()

MAX_DAYS = 366
MAX_PENDING = 200
CHECK_EVERY_S = 30.0
SNOOZE_WINDOW_S = 10 * 60  # "adia" vale até 10 minutos depois de tocar
DEFAULT_SNOOZE_MIN = 10
TIMER_TEXT = "acabou o timer"


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
        self.last_fired: tuple[Reminder, float] | None = None  # para o "adia 10 minutos"

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
        self.last_fired = (reminder, self.clock())
        logger.info("reminder fired", text=reminder.text)
        self.on_fire(reminder.text)

    def cancel(self, rid: int) -> Reminder | None:
        reminder = self._items.pop(rid, None)
        if reminder is not None:
            if reminder.task:
                reminder.task.cancel()
            self._save()
        return reminder

    def cancel_matching(self, which: str) -> list[Reminder]:
        """Cancela pelo texto ("o do mecânico") ou pela posição na lista ("1", "o último")."""
        pending = self.pending
        words = [w for w in re.findall(r"[a-z0-9]+", fold(which)) if w not in _CANCEL_FILLER]
        if len(words) == 1 and (words[0].isdigit() or words[0] in _POSITIONS):
            w = words[0]
            idx = int(w) - 1 if w.isdigit() else _POSITIONS[w]
            chosen = [pending[idx]] if -len(pending) <= idx < len(pending) else []
        else:
            words = [w for w in words if len(w) > 2 or w.isdigit()]
            chosen = [r for r in pending if words and all(w in fold(r.text) for w in words)]
        return [r for r in chosen if self.cancel(r.id) is not None]

    def recently_fired(self, window_s: float = SNOOZE_WINDOW_S) -> Reminder | None:
        if self.last_fired is None:
            return None
        reminder, at = self.last_fired
        return reminder if self.clock() - at <= window_s else None

    def snooze(self, minutes: float = DEFAULT_SNOOZE_MIN) -> Reminder:
        """Marca de novo o lembrete que acabou de tocar."""
        reminder = self.recently_fired()
        if reminder is None:
            raise ValueError("nenhum lembrete tocou agora há pouco")
        self.last_fired = None
        return self.add(minutes, reminder.text)

    def direct_request(self, text: str, now: datetime | None = None) -> ActionCall | None:
        """Reconhece "me lembra às 9 de X", "timer de 5 minutos" e "adia 10 minutos" sem o modelo."""
        f = fold(text)
        if self.recently_fired() is not None and SNOOZE.search(f):
            return ActionCall("adiar_lembrete", {"minutos": round(_snooze_minutes(f), 2)})
        req = parse_reminder(text, now or datetime.fromtimestamp(self.clock()))
        if req is None:
            return None
        return ActionCall("lembrete", {"texto": req.text, "quando": req.due.isoformat(timespec="seconds")})

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


# --------------------------------------------------- o pedido na frase
TRIGGER = re.compile(
    r"(?<![a-z])(?:(?:voce\s+|vc\s+)?(?:pode|poderia|podia|consegue|da\s+pra|tem\s+como)\s+)?(?:"
    r"me\s+lembr(?:a|e|ar)|lembr(?:a|e|ar)[\s-]+me"
    r"|me\s+avis(?:a|e|ar)|avis(?:a|e)[\s-]+me"
    r"|nao\s+(?:me\s+)?deix(?:a|e)\s+(?:eu\s+)?esquecer"
    r"|(?:cria|crie|criar|coloca|coloque|colocar|poe|ponha|bota|bote|botar|faz|faca|fazer|marca|marque|marcar"
    r"|agenda|agende|agendar|liga|ligue|ativa|ative|programa|programe)\s+(?:um\s+|uma\s+)?"
    r"(?P<kind>lembrete|alarme|timer|temporizador|cronometro)"
    r"|(?P<timer>timer|temporizador|cronometro)\s+(?:de|pra|para|por)"
    r"|lembrete\s+(?:pra|para|de)"
    r")(?![a-z])"
)
PAST = re.compile(r"(?<![a-z])(?:ontem|anteontem|semana\s+passada|mes\s+passado|ano\s+passado)(?![a-z])")
# "me lembra o que eu falei", "me lembra quem é": é pergunta, não lembrete.
MEMORY_QUESTION = re.compile(r"\s*(?:o\s+que|do\s+que|qual|quais|quem|como|onde|quando|se\s)")
POLITE = re.compile(r"(?<![a-z])(?:por\s+favor|pfv|pf|pls|akemi)(?![a-z])")
_LEAD = re.compile(r"^(?:[\s,:;.!?-]+|(?:de|do|da|que|pra|para|pro|sobre)(?![a-z]))+")
_TAIL = re.compile(r"(?:[\s,;:.!?-]+|(?<![a-z])(?:ta|ok|beleza|blz|viu)(?![a-z]))+$")
SNOOZE = re.compile(
    r"(?<![a-z])(?:adia|adiar|adie|soneca|mais\s+meia|mais\s+" + NUM + r"|me\s+lembra\s+de\s+novo"
    r"|lembra\s+de\s+novo|depois\s+(?:me\s+)?lembra|daqui\s+a\s+pouco)(?![a-z])"
)
_ANY_DURATION = re.compile(rf"{NUM}\s*{UNIT}|meia(?:\s+|-)hor|\d{{1,2}}\s*h\s*\d")
_POSITIONS = {"primeiro": 0, "proximo": 0, "segundo": 1, "terceiro": 2, "quarto": 3, "ultimo": -1}
_CANCEL_FILLER = {"o", "a", "os", "as", "do", "da", "de", "dos", "das", "pra", "para", "que", "lembrete",
                  "lembretes", "esse", "este", "aquele", "um", "meu"}


@dataclass(frozen=True)
class ReminderRequest:
    due: datetime
    text: str


def parse_reminder(text: str, now: datetime | None = None) -> ReminderRequest | None:
    """Um pedido de lembrete com horário, ou None (aí o modelo responde).

    "lembra que eu gosto de café" (fato) e "me lembra o que eu falei" (pergunta)
    não são lembrete; sem horário também não (o modelo pergunta quando).
    """
    f = fold(text)
    if PAST.search(f):
        return None
    trigger = TRIGGER.search(f)
    if trigger is None or MEMORY_QUESTION.match(f, trigger.end()):
        return None
    when = find_when(text, now)
    if when is None:
        return None
    keep = [True] * len(text)
    for start, end in [trigger.span(), *when.spans, *(m.span() for m in POLITE.finditer(f))]:
        keep[start:end] = [False] * (end - start)
    what = re.sub(r"\s+", " ", "".join(c if k else " " for c, k in zip(text, keep, strict=True))).strip()
    for _ in range(3):  # "de, que" no começo, ", tá?" no fim
        if lead := _LEAD.match(fold(what)):
            what = what[lead.end():]
        if tail := _TAIL.search(fold(what)):
            what = what[: tail.start()]
    what = what.strip()[:200]
    if not what:
        if not (trigger["kind"] in ("timer", "temporizador", "cronometro", "alarme") or trigger["timer"]):
            return None  # "me lembra daqui a 10 minutos": de quê? o modelo pergunta
        what = TIMER_TEXT
    return ReminderRequest(when.due, what)


def _snooze_minutes(f: str) -> float:
    """ "adia 15 minutos" -> 15, "mais meia hora" -> 30, "mais 5" -> 5, "soneca" -> 10."""
    if m := _ANY_DURATION.search(f):
        if seconds := parse_duration(f[m.start():]):
            return seconds / 60
    if m := re.search(rf"mais\s+({NUM})", f):
        return number(m[1])
    return DEFAULT_SNOOZE_MIN


def _say_due(due: datetime, now: datetime) -> str:
    """ "daqui a 10 minutos" para o que é logo; senão "amanhã às 09:00"."""
    left = (due - now).total_seconds()
    if 0 <= left < 59.5:
        n = max(1, round(left))
        return f"daqui a {n} segundo{'s' if n != 1 else ''}"
    if 0 <= left < 3570:
        n = round(left / 60)
        return f"daqui a {n} minuto{'s' if n != 1 else ''}"
    return describe_when(due, now)


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
                return f"Combinado, te lembro {_say_due(when, datetime.fromtimestamp(reminders.clock()))}."
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

    @registry.register(
        "adiar_lembrete", "marca de novo o lembrete que acabou de tocar",
        example=("adia 10 minutos", '[[acao:adiar_lembrete {"minutos": 10}]]'),
        params={"minutos": "daqui a quantos minutos, padrão 10"},
    )
    def adiar_lembrete(minutos: float | str = DEFAULT_SNOOZE_MIN) -> str:
        try:
            minutes = float(str(minutos).replace(",", "."))
        except ValueError:
            minutes = _snooze_minutes(fold(str(minutos)))  # "meia hora", "15 min"
        r = reminders.snooze(minutes)
        now = datetime.fromtimestamp(reminders.clock())
        return f"Tá, te lembro de novo {_say_due(datetime.fromtimestamp(r.due_at), now)}."

    @registry.register(
        "cancelar_lembrete", "cancela um lembrete pendente",
        example=("cancela o lembrete do mecânico", '[[acao:cancelar_lembrete {"qual": "mecânico"}]]'),
        params={"qual": "parte do texto do lembrete, ou a posição na lista (1, 2, último)"},
    )
    def cancelar_lembrete(qual: str) -> str:
        gone = reminders.cancel_matching(str(qual))
        if not gone:
            raise ValueError(f"não achei lembrete de {qual}")
        if len(gone) == 1:
            return f"Cancelei o lembrete de {gone[0].text}."
        return f"Cancelei {len(gone)} lembretes."

    @registry.register("cancelar_lembretes", "cancela todos os lembretes pendentes")
    def cancelar_lembretes() -> str:
        return f"Cancelei {reminders.cancel_all()} lembrete(s)."
