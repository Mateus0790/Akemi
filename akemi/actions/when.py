"""Entende "quando" em português: "amanhã às 9h", "dia 5 de dezembro", "sexta 18:30".

Também aceita data ISO ("2026-12-05 09:00"), que é o que modelos costumam mandar.
Sem hora, vale 9h. Uma hora que já passou hoje vira amanhã.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

MONTHS = {
    "janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "marco": 3, "mar": 3, "abril": 4, "abr": 4,
    "maio": 5, "mai": 5, "junho": 6, "jun": 6, "julho": 7, "jul": 7, "agosto": 8, "ago": 8,
    "setembro": 9, "set": 9, "outubro": 10, "out": 10, "novembro": 11, "nov": 11,
    "dezembro": 12, "dez": 12,
}
WEEKDAYS = {
    "segunda": 0, "seg": 0, "terca": 1, "ter": 1, "quarta": 2, "qua": 2, "quinta": 3, "qui": 3,
    "sexta": 4, "sex": 4, "sabado": 5, "sab": 5, "domingo": 6, "dom": 6,
}
WEEKDAY_NAMES = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
PERIODS = {"manha": 9, "tarde": 15, "noite": 20, "madrugada": 3}
DEFAULT_HOUR = 9
UNITS = {
    "minuto": 1, "minutos": 1, "min": 1, "mins": 1, "hora": 60, "horas": 60, "h": 60,
    "dia": 1440, "dias": 1440, "semana": 10080, "semanas": 10080,
}
NUMBER_WORDS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10, "quinze": 15, "vinte": 20, "trinta": 30, "meia": 0.5,
}


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[,;]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _time_of_day(text: str) -> tuple[int, int] | None:
    """Hora em "9h", "9h30", "09:30", "9 da noite", "às 9", "meio-dia", "meia-noite"."""
    if re.search(r"\bmeio[- ]dia\b", text):
        return 12, 0
    if re.search(r"\bmeia[- ]noite\b", text):
        return 0, 0
    hour = minute = None
    if m := re.search(r"\b(\d{1,2})\s*(?::(\d{2})|h(\d{2})?\b|horas?\b)(?:\s*e\s*(\d{1,2})\s*(?:min\w*)?)?", text):
        hour, minute = int(m.group(1)), int(m.group(2) or m.group(3) or m.group(4) or 0)
    elif m := re.search(r"\b(\d{1,2})\s+da\s+(?:manha|tarde|noite|madrugada)\b", text):
        hour, minute = int(m.group(1)), 0
    elif m := re.search(r"\b(?:as|a|pelas)\s+(\d{1,2})\b", text):
        hour, minute = int(m.group(1)), 0
    if hour is None:
        return None
    period = re.search(r"\bda\s+(manha|tarde|noite|madrugada)\b", text)
    if period and period.group(1) in ("tarde", "noite") and hour < 12:
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _period_hour(text: str) -> int | None:
    m = re.search(r"\b(?:de|a|na|pela|hoje a)\s+(manha|tarde|noite|madrugada)\b", text)
    return PERIODS[m.group(1)] if m else None


def _relative(text: str, now: datetime) -> datetime | None:
    m = re.search(r"\b(?:daqui a|daqui|em|dentro de)\s+(\d+(?:[.,]\d+)?|[a-z]+)\s*(e meia\s+)?"
                  r"(minutos?|mins?|min|horas?|h|dias?|semanas?)\b(\s*e meia)?", text)
    if m is None:
        return None
    raw = m.group(1).replace(",", ".")
    try:
        count = float(raw)
    except ValueError:
        if raw not in NUMBER_WORDS:
            return None
        count = float(NUMBER_WORDS[raw])
    if m.group(2) or m.group(4):
        count += 0.5
    return now + timedelta(minutes=count * UNITS[m.group(3)])


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """Quando, em hora local. None se não entendeu."""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
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
    t = _plain(raw)
    if (rel := _relative(t, now)) is not None:
        return rel
    tod = _time_of_day(t)
    period = _period_hour(t)
    day: datetime | None = None
    explicit_day = False

    if m := re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = (int(y) + 2000 if y and len(y) == 2 else int(y)) if y else now.year
        day = _date(year, mo, d)
        explicit_day = True
        if day and not y and day.date() < now.date():
            day = _date(year + 1, mo, d)
    elif m := re.search(r"\b(?:dia\s+)?(\d{1,2})\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?\b", t):
        if m.group(2) in MONTHS:
            d, mo = int(m.group(1)), MONTHS[m.group(2)]
            year = int(m.group(3)) if m.group(3) else now.year
            day = _date(year, mo, d)
            explicit_day = True
            if day and not m.group(3) and day.date() < now.date():
                day = _date(year + 1, mo, d)
    elif m := re.search(r"\bdia\s+(\d{1,2})\b", t):
        d = int(m.group(1))
        day = _date(now.year, now.month, d)
        if day is None or day.date() < now.date():
            nxt = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
            day = _date(nxt.year, nxt.month, d)
        explicit_day = True
    elif re.search(r"\bdepois de amanha\b", t):
        day, explicit_day = now + timedelta(days=2), True
    elif re.search(r"\bamanha\b", t):
        day, explicit_day = now + timedelta(days=1), True
    elif re.search(r"\bhoje\b", t):
        day, explicit_day = now, True
    else:
        for name, wd in WEEKDAYS.items():
            if re.search(rf"\b{name}(?:-feira| feira)?\b", t):
                ahead = (wd - now.weekday()) % 7
                if ahead == 0 and (re.search(r"\b(proxim[ao]|que vem)\b", t) or _passed(now, tod, period)):
                    ahead = 7
                day, explicit_day = now + timedelta(days=ahead), True
                break
    if day is None and tod is None and period is None:
        return None
    if day is None:
        day = now
    hour, minute = tod if tod is not None else (period if period is not None else DEFAULT_HOUR, 0)
    if tod is not None and period is not None and hour < 12 and period >= 12:
        hour += 12
    when = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if not explicit_day and when <= now:
        when += timedelta(days=1)  # "às 8h" quando já passou das 8: amanhã
    return when


def _passed(now: datetime, tod: tuple[int, int] | None, period: int | None) -> bool:
    hour, minute = tod if tod is not None else (period if period is not None else DEFAULT_HOUR, 0)
    return now.replace(hour=hour, minute=minute) <= now


def _date(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


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
