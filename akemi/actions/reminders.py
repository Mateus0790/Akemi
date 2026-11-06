"""Lembretes: "me lembra amanhã às 9 de ligar pro mecânico".

O horário é lido direto da frase, sem o modelo: funciona igual com o cérebro
reserva local, que é pequeno e erra conta de minutos. Os lembretes ficam salvos
em ``data/lembretes.json``: reiniciar a Akemi não apaga nada, e o que venceu
enquanto ela estava desligada é avisado quando ela volta.

Entende (em pt-BR, escrito ou transcrito pelo Whisper):

- "daqui a 10 minutos", "em meia hora", "daqui a 1h30", "em uma hora e meia"
- "às 18h30", "às 9", "às nove e meia", "ao meio-dia", "às 3 da tarde"
- "amanhã", "depois de amanhã", "hoje à noite", "na sexta às 10"
- "timer de 5 minutos"

Depois que um lembrete toca, "adia 10 minutos" (ou "soneca") marca de novo.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from .registry import ActionCall, ActionRegistry

logger = structlog.get_logger()

MAX_DAYS = 30
SNOOZE_WINDOW_S = 10 * 60  # "adia" vale até 10 minutos depois de tocar
DEFAULT_SNOOZE_MIN = 10
LATE_AFTER_S = 120  # tocou mais de 2 min depois da hora (PC dormindo, ela desligada)


def fold(text: str) -> str:
    """Minúsculas e sem acento, com o mesmo tamanho do original (as posições
    batem, para tirar o horário do texto original)."""
    out = []
    for c in text:
        base = unicodedata.normalize("NFKD", c)[:1] or c
        low = base.lower()
        out.append(low if len(low) == 1 else base)
    return "".join(out)


# ----------------------------------------------------------------- números
_WORDS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13,
    "catorze": 14, "quatorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17,
    "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30, "quarenta": 40,
    "cinquenta": 50, "sessenta": 60, "noventa": 90, "cem": 100,
}
_UNITS = "um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove"
_SIMPLE = "|".join(sorted(_WORDS, key=len, reverse=True))
NUM = rf"(?:\d+(?:[.,]\d+)?|(?:(?:vinte|trinta|quarenta|cinquenta)\s+e\s+(?:{_UNITS})|{_SIMPLE})(?![a-z]))"
_HOUR_WORDS = "uma|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze"


def number(word: str) -> float:
    word = word.strip()
    if word[:1].isdigit():
        return float(word.replace(",", "."))
    return float(sum(_WORDS[w] for w in re.split(r"\s+e\s+", word)))


# ------------------------------------------------------- "daqui a 10 minutos"
UNIT = r"(?:segundos?|seg|s|minutinhos?|minutos?|mins?|m|horinhas?|horas?|hrs?|h|dias?|semanas?)(?![a-z])"
_UNIT_S = (
    (re.compile(r"^(?:segundos?|seg|s)$"), 1),
    (re.compile(r"^(?:minutinhos?|minutos?|mins?|m)$"), 60),
    (re.compile(r"^(?:horinhas?|horas?|hrs?|h)$"), 3600),
    (re.compile(r"^dias?$"), 86400),
    (re.compile(r"^semanas?$"), 7 * 86400),
)
SOON = (
    r"(?<![a-z])(?:daqui\s+a|daqui|dentro\s+de|em|apos|depois\s+de"
    r"|(?:timer|temporizador|cronometro|alarme)\s+(?:de|pra|para|por))\s+(?:uns\s+|umas\s+)?"
)
REL_HM = re.compile(SOON + r"(\d{1,2})\s*h\s*(\d{1,2})(?![0-9])")
REL_HALF = re.compile(SOON + r"(?:uma\s+)?meia(?:\s+|-)hor(?:a|inha)(?![a-z])")
REL = re.compile(
    SOON + rf"(?P<n1>{NUM})\s*(?P<u1>{UNIT})"
    rf"(?:\s+e\s+(?:(?P<half>meia)(?![a-z])|(?P<n2>{NUM})(?:\s*(?P<u2>{UNIT}))?))?"
)


def unit_seconds(unit: str) -> int:
    for pattern, seconds in _UNIT_S:
        if pattern.match(unit):
            return seconds
    return 60


def parse_duration(text: str) -> float | None:
    """Segundos de "10 minutos", "meia hora", "1h30" (sem o "daqui a")."""
    return _relative(fold("daqui a " + text.strip()))[0] if text.strip() else None


def _relative(f: str) -> tuple[float | None, list[tuple[int, int]]]:
    if m := REL_HM.search(f):
        return (int(m[1]) * 60 + int(m[2])) * 60.0, [m.span()]
    if m := REL_HALF.search(f):
        return 1800.0, [m.span()]
    if m := REL.search(f):
        s1 = unit_seconds(m["u1"])
        seconds = number(m["n1"]) * s1
        if m["half"]:
            seconds += s1 / 2
        elif m["n2"]:
            # "uma hora e 15": sem unidade, é a próxima menor
            s2 = unit_seconds(m["u2"]) if m["u2"] else (60 if s1 == 3600 else 1 if s1 == 60 else s1)
            seconds += number(m["n2"]) * s2
        return seconds, [m.span()]
    return None, []


# ----------------------------------------------------- "amanhã às 9 da manhã"
_W = r"(?<![a-z])"
_E = r"(?![a-z])"
NOON = re.compile(_W + r"(?:(?:ao|a|as|pro|pra|para\s+o|ate\s+o)\s+)?meio[\s-]?dia(?:\s+e\s+meia)?" + _E)
MIDNIGHT = re.compile(_W + r"(?:(?:a|as|pra|para\s+a|ate\s+a)\s+)?meia[\s-]?noite(?:\s+e\s+meia)?" + _E)
# "e meia", "e 15", "e quinze", "e quarenta e cinco"
_AND_MIN = rf"(?:\s+e\s+(?P<mw>meia|\d{{1,2}}|(?:vinte|trinta|quarenta|cinquenta)(?:\s+e\s+(?:{_UNITS}))?|quinze|dez|cinco){_E})?"
# "às 9", "pras 18h", "lá pelas 7"; o "às" vira "as" sem acento.
# depois da hora: "9 horas", "9h", "9h30", "18:30"
_HOUR_TAIL = r"(?:\s*(?:horas?|hrs?)(?![a-z])|\s*(?:h|:|\.)\s*(?P<m>\d{2})?(?:min)?(?![0-9a-z]))"
AT_WORD = re.compile(
    _W + r"(?:as|pras|pelas|ate\s+as|para\s+as|depois\s+das|a\s+partir\s+das|por\s+volta\s+das)\s+"
    rf"(?P<h>\d{{1,2}}(?![0-9])|(?:{_HOUR_WORDS})(?![a-z]))" + _HOUR_TAIL + "?" + _AND_MIN
)
# "à uma", "à 1h"
AT_ONE = re.compile(_W + r"a\s+(?P<h>uma(?![a-z])|1(?![0-9]))" + _HOUR_TAIL + "?" + _AND_MIN)
# "18h30", "18:30", "9h", "9 horas" sem preposição
CLOCK = re.compile(r"(?<![\d:.,])(?P<h>\d{1,2})" + _HOUR_TAIL + _AND_MIN)
# Depois de "às 9" sem "h", só vale como horário se vier algo que não é um
# substantivo: "às 9 de ligar" sim, "as 2 coisas" não.
_AFTER_BARE = re.compile(
    r"\s*(?:$|[,.;:!?]|e\s|d[aoe]\s|a\s+(?:tarde|noite)|hoje|amanha|depois|pr[ao]s?\s|para\s|que\s|me\s"
    r"|n[ao]\s|em\s|eu\s|tem\s|tenho\s|vou\s|preciso\s)"
)
PERIOD = re.compile(_W + r"(?:(?:d[aoe]|a|pela|nest[ae]|ness[ae]|essa|esta|hoje\s+a|hoje\s+de)\s+)(?P<p>manha|tarde|noite|madrugada)" + _E)
PERIOD_HOUR = {"manha": 9, "tarde": 15, "noite": 20, "madrugada": 3}
DAY_AFTER = re.compile(_W + r"depois\s+de\s+amanha" + _E)
TOMORROW = re.compile(_W + r"amanha" + _E)
TODAY = re.compile(_W + r"hoje" + _E)
_DAYS = {"segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sabado": 5, "domingo": 6}
WEEKDAY = re.compile(
    _W + r"(?P<pre>(?:n[ao]|nest[ae]|ness[ae]|est[ae]|ess[ae]|ate|n[ao]\s+proxim[ao]|proxim[ao]|a|o|da|do|uma|um|sua|seu|minha|meu|pela|pelo)\s+)?"
    r"(?P<d>segunda|terca|quarta|quinta|sexta|sabado|domingo)(?P<feira>[\s-]feira)?" + _E
)
_NOT_A_DAY = {"a", "o", "da", "do", "uma", "um", "sua", "seu", "minha", "meu", "pela", "pelo"}


@dataclass(frozen=True)
class When:
    due: dt.datetime
    spans: tuple[tuple[int, int], ...]


def _minutes_word(mw: str | None) -> int:
    if not mw:
        return 0
    return 30 if mw == "meia" else int(number(mw))


def _clock_time(f: str) -> tuple[int, int, tuple[int, int]] | None:
    """(hora, minuto, trecho) do horário dito, ou None."""
    if m := NOON.search(f):
        return 12, 30 if m[0].endswith("meia") else 0, m.span()
    if m := MIDNIGHT.search(f):
        return 0, 30 if m[0].endswith("meia") else 0, m.span()
    for pattern in (AT_WORD, AT_ONE, CLOCK):
        for m in pattern.finditer(f):
            h = int(number(m["h"]))
            minute = int(m["m"]) if m["m"] else _minutes_word(m["mw"])
            if h > 23 or minute > 59:
                continue
            explicit = m["m"] is not None or re.match(r"\s*(?:h|:|\.)", f[m.end("h"):]) is not None
            if pattern is AT_WORD and not explicit and not m["mw"] and not _AFTER_BARE.match(f, m.end()):
                continue  # "as 2 coisas": é artigo, não horário
            return h, minute, m.span()
    return None


def parse_when(text: str, now: dt.datetime | None = None) -> When | None:
    """Quando, lido do texto: o momento e os trechos que diziam isso. None se
    não diz quando (ou se o horário já passou, como "hoje às 9" de noite)."""
    now = now or dt.datetime.now()
    f = fold(text)
    seconds, spans = _relative(f)
    if seconds is not None:
        if seconds <= 0:
            return None
        return When(now + dt.timedelta(seconds=seconds), tuple(spans))

    spans = []
    day: int | None = None  # dias a partir de hoje
    weekday: int | None = None
    next_week = False
    if m := DAY_AFTER.search(f):
        day = 2
        spans.append(m.span())
    elif m := TOMORROW.search(f):
        day = 1
        spans.append(m.span())
    elif m := TODAY.search(f):
        day = 0
        spans.append(m.span())
    else:
        for m in WEEKDAY.finditer(f):
            pre = (m["pre"] or "").strip()
            if pre in _NOT_A_DAY and not m["feira"]:
                continue  # "a segunda dose", "o sábado passado"
            weekday = _DAYS[m["d"]]
            next_week = "proxim" in pre
            spans.append(m.span())
            break

    period = None
    if m := PERIOD.search(f):
        period = m["p"]
        spans.append(m.span())

    clock = _clock_time(f)
    if clock is None and day is None and weekday is None and period is None:
        return None
    if clock is None:
        hour, minute = PERIOD_HOUR.get(period or "", 9), 0
    else:
        hour, minute, span = clock
        spans.append(span)
        if period in ("tarde", "noite") and hour < 12:
            hour += 12
    # "às 3" sem "da tarde": pode ser 3h ou 15h
    either = clock is not None and period is None and 1 <= hour <= 11

    if weekday is not None:
        day = (weekday - now.weekday()) % 7
        if next_week and day == 0:
            day = 7
    base = (now + dt.timedelta(days=day or 0)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidates = [base, base + dt.timedelta(hours=12)] if either else [base]

    if day is None:
        # Sem dia: o próximo horário assim. "às 3" depois do almoço é 15h.
        candidates += [c + dt.timedelta(days=1) for c in candidates]
    elif either and (day > 0 or weekday is not None):
        candidates = [base + dt.timedelta(hours=12)] if hour <= 6 else [base]  # "amanhã às 3" é de tarde
    future = [c for c in candidates if c > now]
    if not future and weekday is not None:
        future = [base + dt.timedelta(days=7)]
    if not future:
        return None  # "hoje às 9", dito de noite
    return When(min(future), tuple(spans))


# ------------------------------------------------------------- a frase toda
TRIGGER = re.compile(
    r"(?<![a-z])(?:(?:voce\s+|vc\s+)?(?:pode|poderia|podia|consegue|da\s+pra|tem\s+como)\s+)?(?:"
    r"me\s+lembr(?:a|e|ar)|lembr(?:a|e|ar)[\s-]+me"
    r"|me\s+avis(?:a|e|ar)|avis(?:a|e)[\s-]+me"
    r"|nao\s+(?:me\s+)?deix(?:a|e)\s+(?:eu\s+)?esquecer"
    r"|(?:cria|crie|criar|coloca|coloque|colocar|poe|ponha|bota|bote|botar|faz|faca|fazer|marca|marque|marcar"
    r"|agenda|agende|agendar|liga|ligue|ativa|ative|programa|programe)\s+(?:um\s+|uma\s+)?"
    r"(?:lembrete|alarme|timer|temporizador|cronometro)"
    r"|(?:timer|temporizador|cronometro)\s+(?:de|pra|para|por)"
    r"|lembrete\s+(?:pra|para|de)"
    r")(?![a-z])"
)
PAST = re.compile(r"(?<![a-z])(?:ontem|anteontem|semana\s+passada|mes\s+passado|ano\s+passado)(?![a-z])")
# "me lembra o que eu falei", "me lembra quem é": é pergunta, não lembrete.
MEMORY_QUESTION = re.compile(r"\s*(?:o\s+que|do\s+que|qual|quais|quem|como|onde|quando|se\s)")
POLITE = re.compile(r"(?<![a-z])(?:por\s+favor|pfv|pf|pls|akemi)(?![a-z])")
_LEAD = re.compile(r"^(?:[\s,:;.!?-]+|(?:de|do|da|que|pra|para|pro|sobre)(?![a-z]))+")
_TAIL = re.compile(r"(?:[\s,;:.!?-]+|(?<![a-z])(?:ta|ok|beleza|blz|viu)(?![a-z]))+$")


@dataclass(frozen=True)
class ReminderRequest:
    due: dt.datetime
    text: str


def parse_reminder(text: str, now: dt.datetime | None = None) -> ReminderRequest | None:
    """Um pedido de lembrete com horário, ou None (aí o modelo responde)."""
    f = fold(text)
    if PAST.search(f):
        return None
    trigger = TRIGGER.search(f)
    if trigger is None or MEMORY_QUESTION.match(f, trigger.end()):
        return None
    when = parse_when(text, now)
    if when is None:
        return None
    drop = [trigger.span(), *when.spans, *(m.span() for m in POLITE.finditer(f))]
    keep = [True] * len(text)
    for start, end in drop:
        keep[start:end] = [False] * (end - start)
    what = "".join(c if k else " " for c, k in zip(text, keep, strict=True))
    what = re.sub(r"\s+", " ", what).strip()
    for _ in range(3):  # "de, que" no começo, ", tá?" no fim
        if lead := _LEAD.match(fold(what)):
            what = what[lead.end():]
        if tail := _TAIL.search(fold(what)):
            what = what[: tail.start()]
    return ReminderRequest(when.due, what.strip()[:200])


_POSITIONS = {"primeiro": 0, "proximo": 0, "segundo": 1, "terceiro": 2, "quarto": 3, "ultimo": -1}
_CANCEL_FILLER = {"o", "a", "os", "as", "do", "da", "de", "dos", "das", "pra", "para", "que", "lembrete",
                  "lembretes", "esse", "este", "aquele", "um", "meu"}

SNOOZE = re.compile(
    r"(?<![a-z])(?:adia|adiar|adie|soneca|mais\s+meia|mais\s+" + NUM + r"|me\s+lembra\s+de\s+novo|lembra\s+de\s+novo"
    r"|depois\s+(?:me\s+)?lembra|daqui\s+a\s+pouco)(?![a-z])"
)


# -------------------------------------------------------------- como falar
WEEKDAY_NAMES = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def say_clock(moment: dt.datetime) -> str:
    """ "às 18h30", "às 9h", "ao meio-dia", "à meia-noite", "à 1h"."""
    h, m = moment.hour, moment.minute
    if (h, m) == (12, 0):
        return "ao meio-dia"
    if (h, m) == (0, 0):
        return "à meia-noite"
    clock = f"{h}h{m:02d}" if m else f"{h}h"
    return f"à {clock}" if h == 1 else f"às {clock}"


def say_when(due: float, now: float | None = None) -> str:
    """Como ela diz o horário: "daqui a 10 minutos", "hoje às 18h30", "na sexta às 10h"."""
    now = time.time() if now is None else now
    delta = due - now
    if 0 <= delta < 59.5:
        s = max(1, round(delta))
        return f"daqui a {s} segundo{'s' if s != 1 else ''}"
    if 0 <= delta < 3570:
        m = round(delta / 60)
        return f"daqui a {m} minuto{'s' if m != 1 else ''}"
    d, n = dt.datetime.fromtimestamp(due), dt.datetime.fromtimestamp(now)
    days = (d.date() - n.date()).days
    day = {0: "hoje", 1: "amanhã", 2: "depois de amanhã", -1: "ontem"}.get(days)
    if day is None:
        if 0 < days < 7:
            wd = d.weekday()
            day = f"{'no' if wd >= 5 else 'na'} {WEEKDAY_NAMES[wd]}"
        else:
            day = f"dia {d:%d/%m}"
    return f"{day} {say_clock(d)}"


# ------------------------------------------------------------ a agenda
@dataclass
class Reminder:
    id: int
    text: str
    due_at: float
    created_at: float = field(default_factory=time.time)
    task: asyncio.Task | None = field(default=None, repr=False, compare=False)

    def to_json(self) -> dict:
        return {"id": self.id, "text": self.text, "due_at": self.due_at, "created_at": self.created_at}


class Reminders:
    """Agenda os lembretes e salva em disco (``path``), se houver.

    ``on_fire(reminder, late)`` é chamado na hora; ``late`` é True quando passou
    da hora (ela estava desligada ou o PC dormindo).
    """

    def __init__(
        self,
        on_fire: Callable[[Reminder, bool], None],
        path: Path | None = None,
        *,
        max_days: float = MAX_DAYS,
        clock: Callable[[], float] = time.time,
        tick_s: float = 30.0,
    ) -> None:
        self.on_fire = on_fire
        self.path = path
        self.max_days = max_days
        self.clock = clock
        self.tick_s = tick_s  # confere o relógio de tempos em tempos: o PC pode dormir
        self._items: dict[int, Reminder] = {}
        self._next_id = 1
        self.last_fired: tuple[Reminder, float] | None = None
        self._load()

    @property
    def max_minutes(self) -> float:
        return self.max_days * 24 * 60

    @property
    def pending(self) -> list[Reminder]:
        return sorted(self._items.values(), key=lambda r: r.due_at)

    # ------------------------------------------------------------ disco
    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for raw in data.get("lembretes", []):
                r = Reminder(int(raw["id"]), str(raw["text"]), float(raw["due_at"]), float(raw.get("created_at", 0)))
                self._items[r.id] = r
            self._next_id = max([int(data.get("proximo_id", 1)), *(r.id + 1 for r in self._items.values())])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("reminders file unreadable, starting empty", path=str(self.path), error=str(exc))

    def _save(self) -> None:
        if self.path is None:
            return
        data = {"proximo_id": self._next_id, "lembretes": [r.to_json() for r in self.pending]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning("could not save reminders", error=str(exc))

    # ------------------------------------------------------------ ciclo
    def start(self) -> None:
        """Agenda os que vieram do disco. Os que venceram com ela desligada tocam já."""
        for r in self.pending:
            if r.task is None:
                self._schedule(r)

    def stop(self) -> None:
        """Para os relógios sem apagar nada (continuam salvos)."""
        for r in self._items.values():
            if r.task is not None:
                r.task.cancel()
                r.task = None

    def _schedule(self, r: Reminder) -> None:
        try:
            r.task = asyncio.get_running_loop().create_task(self._wait(r))
        except RuntimeError:
            r.task = None  # sem loop rodando: ``start()`` agenda depois

    async def _wait(self, r: Reminder) -> None:
        try:
            while (left := r.due_at - self.clock()) > 0:
                await asyncio.sleep(min(left, self.tick_s))
        except asyncio.CancelledError:
            return
        if self._items.pop(r.id, None) is None:
            return
        self._save()
        late = self.clock() - r.due_at > LATE_AFTER_S
        self.last_fired = (r, self.clock())
        logger.info("reminder fired", text=r.text, late=late)
        try:
            self.on_fire(r, late)
        except Exception:
            logger.exception("reminder callback failed")

    # ---------------------------------------------------------- mudanças
    def add_at(self, due_at: float, text: str) -> Reminder:
        left = due_at - self.clock()
        if left <= 0:
            raise ValueError("esse horário já passou")
        if left > self.max_days * 86400:
            raise ValueError(f"só marco lembretes até {self.max_days:g} dias pra frente")
        rid, self._next_id = self._next_id, self._next_id + 1
        r = Reminder(rid, text.strip(), due_at, self.clock())
        self._items[rid] = r
        self._save()
        self._schedule(r)
        return r

    def add(self, minutes: float, text: str) -> Reminder:
        if not 0 < minutes <= self.max_minutes:
            raise ValueError(f"lembrete precisa ser entre 0 e {int(self.max_minutes)} minutos")
        return self.add_at(self.clock() + minutes * 60, text)

    def _drop(self, rs: list[Reminder]) -> list[Reminder]:
        for r in rs:
            if r.task is not None:
                r.task.cancel()
            self._items.pop(r.id, None)
        if rs:
            self._save()
        return rs

    def cancel(self, which: str) -> list[Reminder]:
        """Cancela pelo texto ("o do mecânico") ou pela posição na lista ("1", "o último")."""
        pending = self.pending
        words = [w for w in re.findall(r"[a-z0-9]+", fold(which)) if w not in _CANCEL_FILLER]
        if len(words) == 1 and (words[0].isdigit() or words[0] in _POSITIONS):
            w = words[0]
            idx = int(w) - 1 if w.isdigit() else _POSITIONS[w]
            return self._drop([pending[idx]]) if -len(pending) <= idx < len(pending) else []
        words = [w for w in words if len(w) > 2 or w.isdigit()]
        if not words:
            return []
        return self._drop([r for r in pending if all(w in fold(r.text) for w in words)])

    def cancel_all(self) -> int:
        return len(self._drop(list(self._items.values())))

    def recently_fired(self, window_s: float = SNOOZE_WINDOW_S) -> Reminder | None:
        if self.last_fired is None:
            return None
        r, at = self.last_fired
        return r if self.clock() - at <= window_s else None

    def snooze(self, minutes: float = DEFAULT_SNOOZE_MIN) -> Reminder:
        r = self.recently_fired()
        if r is None:
            raise ValueError("nenhum lembrete tocou agora há pouco")
        self.last_fired = None
        return self.add(minutes, r.text)

    # -------------------------------------------------- pedido sem modelo
    def direct_request(self, text: str, now: dt.datetime | None = None) -> ActionCall | None:
        """Reconhece "me lembra às 9 de X" e "adia 10 minutos" sem o modelo."""
        f = fold(text)
        if self.recently_fired() is not None and SNOOZE.search(f):
            return ActionCall("adiar_lembrete", {"minutos": round(_snooze_minutes(f), 2)})
        req = parse_reminder(text, now)
        if req is None:
            return None
        return ActionCall("lembrete", {"texto": req.text, "timestamp": req.due.timestamp()})

    def describe(self) -> str:
        pending = self.pending
        if not pending:
            return "Nenhum lembrete marcado."
        now = self.clock()
        items = [f"{say_when(r.due_at, now)}, {r.text or 'o timer'}" for r in pending]
        n = len(items)
        return f"Tenho {n} lembrete{'s' if n != 1 else ''}: " + "; ".join(items) + "."


_ANY_DURATION = re.compile(rf"{NUM}\s*{UNIT}|meia(?:\s+|-)hor|\d{{1,2}}\s*h\s*\d")


def _snooze_minutes(f: str) -> float:
    """ "adia 15 minutos" -> 15, "mais meia hora" -> 30, "mais 5" -> 5, "soneca" -> 10."""
    if m := _ANY_DURATION.search(f):
        if seconds := parse_duration(f[m.start():]):
            return seconds / 60
    if m := re.search(rf"mais\s+({NUM})", f):
        return number(m[1])
    return DEFAULT_SNOOZE_MIN


def resolve_when(quando: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """O ``quando`` que o modelo escreveu ("amanhã às 9h", "10 minutos", "18:30")."""
    q = quando.strip()
    if not q:
        return None
    for variant in (q, f"daqui a {q}", f"às {q}"):
        if when := parse_when(variant, now):
            return when.due
    return None


def register_reminder_actions(registry: ActionRegistry, reminders: Reminders) -> None:
    @registry.register(
        "lembrete", "agenda um lembrete que você avisa em voz alta na hora (fica salvo)",
        example=('me lembra amanhã às 9 de ligar pro mecânico',
                 '[[acao:lembrete {"quando": "amanhã às 9h", "texto": "ligar pro mecânico"}]]'),
        params={"quando": "quando avisar, como ele falou: 'daqui a 10 minutos', 'amanhã às 9h', 'sexta às 18h'",
                "texto": "do que lembrar"},
    )
    def lembrete(texto: str = "", quando: str = "", minutos: float | str | None = None,
                 timestamp: float | None = None) -> str:
        if timestamp is not None:
            r = reminders.add_at(float(timestamp), texto)
        elif str(quando).strip():
            due = resolve_when(str(quando))
            if due is None:
                raise ValueError(f"não entendi quando é '{quando}'")
            r = reminders.add_at(due.timestamp(), texto)
        elif minutos is not None and str(minutos).strip():
            try:
                minutes = float(str(minutos).replace(",", "."))
            except ValueError as exc:
                raise ValueError("não entendi em quantos minutos") from exc
            r = reminders.add(minutes, texto)
        else:
            raise ValueError("faltou dizer quando")
        return f"Combinado, te lembro {say_when(r.due_at, reminders.clock())}."

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
        return f"Tá, te lembro de novo {say_when(r.due_at, reminders.clock())}."

    @registry.register(
        "listar_lembretes", "diz quais lembretes estão marcados", speak_result=True,
        example=("quais lembretes eu tenho?", "[[acao:listar_lembretes]]"),
    )
    def listar_lembretes() -> str:
        return reminders.describe()

    @registry.register(
        "cancelar_lembrete", "cancela um lembrete marcado",
        example=("cancela o lembrete do mecânico", '[[acao:cancelar_lembrete {"qual": "mecânico"}]]'),
        params={"qual": "parte do texto do lembrete, ou a posição na lista (1, 2, último)"},
    )
    def cancelar_lembrete(qual: str) -> str:
        gone = reminders.cancel(str(qual))
        if not gone:
            raise ValueError(f"não achei lembrete de {qual}")
        if len(gone) == 1:
            return f"Cancelei o lembrete de {gone[0].text or 'timer'}."
        return f"Cancelei {len(gone)} lembretes."

    @registry.register("cancelar_lembretes", "cancela todos os lembretes pendentes")
    def cancelar_lembretes() -> str:
        return f"Cancelei {reminders.cancel_all()} lembrete(s)."
