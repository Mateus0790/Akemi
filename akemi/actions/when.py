"""Entende "quando" em português: "amanhã às 9h", "dia 5 de dezembro", "sexta 18:30".

Também aceita data ISO ("2026-12-05 09:00"), que é o que modelos costumam mandar.
Sem hora, vale 9h. Uma hora que já passou hoje vira amanhã.

``find_when`` acha o horário no meio de uma frase ("me lembra de tirar a pizza
daqui a dez minutos") e diz onde ele está, para o resto virar o texto do lembrete.

Horas com "h" ou ":" ("8h", "18:30") valem como estão. Uma hora solta ("às 3",
"às nove") é a próxima que vier: "às 3" depois do almoço é 15h. Em outro dia,
de 1 a 6 é de tarde ("amanhã às 3") e de 7 a 11, de manhã.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

MONTHS = {
    "janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "marco": 3, "mar": 3, "abril": 4, "abr": 4,
    "maio": 5, "mai": 5, "junho": 6, "jun": 6, "julho": 7, "jul": 7, "agosto": 8, "ago": 8,
    "setembro": 9, "set": 9, "outubro": 10, "out": 10, "novembro": 11, "nov": 11,
    "dezembro": 12, "dez": 12,
}
WEEKDAYS = {"segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sabado": 5, "domingo": 6}
WEEKDAY_NAMES = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
PERIODS = {"manha": 9, "tarde": 15, "noite": 20, "madrugada": 3}
DEFAULT_HOUR = 9


def fold(text: str) -> str:
    """Minúsculas e sem acento, com o mesmo tamanho do original: as posições batem."""
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


def relative_seconds(f: str) -> tuple[float | None, tuple[int, int] | None]:
    """Segundos de "daqui a 10 minutos" no texto (já em ``fold``) e onde está."""
    if m := REL_HM.search(f):
        return (int(m[1]) * 60 + int(m[2])) * 60.0, m.span()
    if m := REL_HALF.search(f):
        return 1800.0, m.span()
    if m := REL.search(f):
        s1 = unit_seconds(m["u1"])
        seconds = number(m["n1"]) * s1
        if m["half"]:
            seconds += s1 / 2
        elif m["n2"]:
            # "uma hora e 15": sem unidade, é a próxima menor
            s2 = unit_seconds(m["u2"]) if m["u2"] else (60 if s1 == 3600 else 1 if s1 == 60 else s1)
            seconds += number(m["n2"]) * s2
        return seconds, m.span()
    return None, None


def parse_duration(text: str) -> float | None:
    """Segundos de "10 minutos", "meia hora", "1h30" (sem o "daqui a")."""
    return relative_seconds(fold("daqui a " + text.strip()))[0] if text.strip() else None


# ------------------------------------------------------- "às 9 da manhã"
_W = r"(?<![a-z])"
_E = r"(?![a-z])"
NOON = re.compile(_W + r"(?:(?:ao|a|as|pro|pra|para\s+o|ate\s+o)\s+)?meio[\s-]?dia(?:\s+e\s+meia)?" + _E)
MIDNIGHT = re.compile(_W + r"(?:(?:a|as|pra|para\s+a|ate\s+a)\s+)?meia[\s-]?noite(?:\s+e\s+meia)?" + _E)
# "e meia", "e 15", "e quinze", "e quarenta e cinco", "e 15 minutos"
_AND_MIN = (
    rf"(?:\s+e\s+(?P<mw>meia|\d{{1,2}}|(?:vinte|trinta|quarenta|cinquenta)(?:\s+e\s+(?:{_UNITS}))?|quinze|dez|cinco)"
    rf"(?:\s*min\w*)?{_E})?"
)
# depois da hora: "9 horas", "9h", "9h30", "18:30"
_HOUR_TAIL = r"(?:\s*(?:horas?|hrs?)(?![a-z])|\s*(?:h|:|\.)\s*(?P<m>\d{2})?(?:min)?(?![0-9a-z]))"
_HOUR = rf"(?P<h>\d{{1,2}}(?![0-9])|(?:{_HOUR_WORDS})(?![a-z]))"
# "às 9", "pras 18h", "lá pelas 7"; o "às" vira "as" sem acento.
AT_WORD = re.compile(
    _W + r"(?:as|pras|pelas|ate\s+as|para\s+as|depois\s+das|a\s+partir\s+das|por\s+volta\s+das)\s+"
    + _HOUR + _HOUR_TAIL + "?" + _AND_MIN
)
# "à uma", "à 1h"
AT_ONE = re.compile(_W + r"a\s+(?P<h>uma(?![a-z])|1(?![0-9]))" + _HOUR_TAIL + "?" + _AND_MIN)
# "18h30", "18:30", "9h", "9 horas" sem preposição
CLOCK = re.compile(r"(?<![\d:./])(?P<h>\d{1,2})" + _HOUR_TAIL + _AND_MIN)
# "3 da tarde", "nove da noite"
HOUR_PERIOD = re.compile(r"(?<![\d:./a-z])" + _HOUR + _AND_MIN + r"(?=\s+d[aoe]\s+(?:manha|tarde|noite|madrugada))")
# Depois de "às 9" sem "h", só vale como horário se vier algo que não é um
# substantivo: "às 9 de ligar" sim, "as 2 coisas" não.
_AFTER_BARE = re.compile(
    r"\s*(?:$|[,.;:!?]|e\s|d[aoe]\s|a\s+(?:tarde|noite)|hoje|amanha|depois|pr[ao]s?\s|para\s|que\s|me\s"
    r"|n[ao]\s|em\s|eu\s|tem\s|tenho\s|vou\s|preciso\s)"
)
PERIOD = re.compile(
    _W + r"(?:(?:d[aoe]|a|na|pela|nest[ae]|ness[ae]|essa|esta|hoje\s+a|hoje\s+de)\s+)(?P<p>manha|tarde|noite|madrugada)" + _E
)
DAY_AFTER = re.compile(_W + r"depois\s+de\s+amanha" + _E)
TOMORROW = re.compile(_W + r"amanha" + _E)
TODAY = re.compile(_W + r"hoje" + _E)
WEEKDAY = re.compile(
    _W + r"(?P<pre>(?:n[ao]|nest[ae]|ness[ae]|est[ae]|ess[ae]|ate|n[ao]\s+proxim[ao]|proxim[ao]|a|o|da|do|uma|um"
    r"|sua|seu|minha|meu|pela|pelo)\s+)?"
    r"(?P<d>segunda|terca|quarta|quinta|sexta|sabado|domingo)(?P<feira>[\s-]feira)?(?P<next>\s+que\s+vem)?" + _E
)
_NOT_A_DAY = {"a", "o", "da", "do", "uma", "um", "sua", "seu", "minha", "meu", "pela", "pelo"}
_MONTH_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_SLASH = re.compile(r"(?<![\d/])(?:dia\s+)?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?![\d/])")
DATE_MONTH = re.compile(_W + rf"(?:dia\s+)?(\d{{1,2}})\s+de\s+({_MONTH_NAMES})(?:\s+de\s+(\d{{4}}))?" + _E)
DATE_DAY = re.compile(_W + r"dia\s+(\d{1,2})(?![\d/])")


@dataclass(frozen=True)
class When:
    due: datetime
    spans: tuple[tuple[int, int], ...]  # trechos do texto que diziam quando


@dataclass(frozen=True)
class _Clock:
    hour: int
    minute: int
    span: tuple[int, int]
    bare: bool  # "às 3", sem "h" nem ":": pode ser 3h ou 15h


def _minutes_word(mw: str | None) -> int:
    if not mw:
        return 0
    return 30 if mw == "meia" else int(number(mw))


def _clock_time(f: str) -> _Clock | None:
    if m := NOON.search(f):
        return _Clock(12, 30 if m[0].endswith("meia") else 0, m.span(), False)
    if m := MIDNIGHT.search(f):
        return _Clock(0, 30 if m[0].endswith("meia") else 0, m.span(), False)
    for pattern in (AT_WORD, AT_ONE, CLOCK, HOUR_PERIOD):
        for m in pattern.finditer(f):
            h = int(number(m["h"]))
            has_m = "m" in m.groupdict() and m["m"] is not None
            minute = int(m["m"]) if has_m else _minutes_word(m["mw"])
            if h > 23 or minute > 59:
                continue
            explicit = has_m or re.match(r"\s*(?:h|:|\.)", f[m.end("h"):]) is not None
            if pattern is AT_WORD and not explicit and not m["mw"] and not _AFTER_BARE.match(f, m.end()):
                continue  # "as 2 coisas": é artigo, não horário
            return _Clock(h, minute, m.span(), not explicit)
    return None


def _date(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _explicit_date(f: str, now: datetime) -> tuple[datetime | None, tuple[int, int]] | None:
    """Uma data dita ("05/12", "dia 5 de dezembro", "dia 5"): (data ou None se não existe, trecho)."""
    if m := DATE_SLASH.search(f):
        d, mo, y = int(m[1]), int(m[2]), m[3]
        year = (int(y) + 2000 if len(y) == 2 else int(y)) if y else now.year
        day = _date(year, mo, d)
        if day and not y and day.date() < now.date():
            day = _date(year + 1, mo, d)
        return day, m.span()
    if m := DATE_MONTH.search(f):
        d, mo = int(m[1]), MONTHS[m[2]]
        year = int(m[3]) if m[3] else now.year
        day = _date(year, mo, d)
        if day and not m[3] and day.date() < now.date():
            day = _date(year + 1, mo, d)
        return day, m.span()
    if m := DATE_DAY.search(f):
        d = int(m[1])
        day = _date(now.year, now.month, d)
        if day is None or day.date() < now.date():
            nxt = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
            day = _date(nxt.year, nxt.month, d)
        return day, m.span()
    return None


def find_when(text: str, now: datetime | None = None) -> When | None:
    """O horário dito no texto (pode estar no meio de uma frase) e onde ele está.

    None se não diz quando. Uma hora que já passou num dia dito ("hoje às 10h"
    de tarde) volta assim mesmo, no passado: quem chama diz que já passou.
    """
    now = now or datetime.now()
    f = fold(text)
    seconds, span = relative_seconds(f)
    if seconds is not None:
        return When(now + timedelta(seconds=seconds), (span,)) if seconds > 0 else None

    spans: list[tuple[int, int]] = []
    day: int | None = None  # dias a partir de hoje
    weekday: int | None = None
    next_week = False
    if (date := _explicit_date(f, now)) is not None:
        found, span = date
        if found is None:
            return None  # "30/02"
        day = (found.date() - now.date()).days
        spans.append(span)
        f = f[: span[0]] + " " * (span[1] - span[0]) + f[span[1]:]  # o "12" de "05/12" não é hora
    elif m := DAY_AFTER.search(f):
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
            weekday = WEEKDAYS[m["d"]]
            next_week = "proxim" in pre or bool(m["next"])
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
        hour, minute = PERIODS.get(period or "", DEFAULT_HOUR), 0
    else:
        hour, minute = clock.hour, clock.minute
        spans.append(clock.span)
        if period in ("tarde", "noite") and hour < 12:
            hour += 12
    either = clock is not None and clock.bare and period is None and 1 <= hour <= 11

    if weekday is not None:
        day = (weekday - now.weekday()) % 7
        if next_week and day == 0:
            day = 7
    base = (now + timedelta(days=day or 0)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidates = [base, base + timedelta(hours=12)] if either else [base]

    if day is None:
        # Sem dia: o próximo horário assim.
        candidates += [c + timedelta(days=1) for c in candidates]
    elif either and (day > 0 or weekday is not None):
        candidates = [base + timedelta(hours=12)] if hour <= 6 else [base]  # "amanhã às 3" é de tarde
    future = [c for c in candidates if c > now]
    if not future and weekday is not None:
        future = [base + timedelta(days=7)]
    due = min(future) if future else max(candidates)  # dia dito e hora passada: no passado mesmo
    return When(due, tuple(spans))


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """Quando, em hora local. None se não entendeu."""
    now = now or datetime.now()
    if not text or not text.strip():
        return None
    raw = text.strip()
    try:  # ISO, do jeito que o modelo manda
        iso = datetime.fromisoformat(raw.replace("Z", ""))
        if iso.tzinfo is not None:
            iso = iso.astimezone().replace(tzinfo=None)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            iso = iso.replace(hour=DEFAULT_HOUR)
        return iso
    except ValueError:
        pass
    when = find_when(raw, now) or find_when("daqui a " + raw, now)  # "10 minutos", do modelo
    return when.due if when else None


def describe_when(when: datetime, now: datetime | None = None) -> str:
    """ "hoje às 18:30", "amanhã às 09:00", "sexta, 07/11, às 10:00", "05/12/2027 às 09:00"."""
    now = now or datetime.now()
    hour = f"{when:%H:%M}"
    days = (when.date() - now.date()).days
    if days == 0:
        return f"hoje às {hour}"
    if days == 1:
        return f"amanhã às {hour}"
    if 1 < days < 7:
        return f"{WEEKDAY_NAMES[when.weekday()]}, {when:%d/%m}, às {hour}"
    if when.year == now.year:
        return f"{when:%d/%m} às {hour}"
    return f"{when:%d/%m/%Y} às {hour}"
