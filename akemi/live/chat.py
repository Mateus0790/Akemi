"""Chat de live: agrupamento, seleção e limite de respostas.

A Akemi não responde mensagem por mensagem (isso é caro e soa robótico).
O :class:`ChatBatcher` junta as mensagens de uma janela de alguns segundos,
escolhe as mais interessantes e publica **um** evento ``chat.batch``.

Todo evento de chat sai com ``Trust.PUBLIC`` — ou seja, nunca dispara ações.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass, field

import structlog

from akemi.core.events import Event, EventBus, Priority, Trust
from akemi.safety.moderation import Moderator

logger = structlog.get_logger()


@dataclass
class ChatMessage:
    user: str
    text: str
    platform: str = "console"
    ts: float = field(default_factory=time.time)
    is_mod: bool = False
    is_sub: bool = False
    first_msg: bool = False
    flags: list[str] = field(default_factory=list)


class ChatBatcher:
    def __init__(
        self,
        bus: EventBus,
        moderator: Moderator,
        *,
        name: str = "akemi",
        window_s: float = 6.0,
        max_selected: int = 3,
        max_batches_per_minute: int = 6,
        user_cooldown_s: float = 30.0,
    ) -> None:
        self.bus = bus
        self.moderator = moderator
        self.name = name.lower()
        self.window_s = window_s
        self.max_selected = max_selected
        self.max_batches_per_minute = max_batches_per_minute
        self.user_cooldown_s = user_cooldown_s
        self._pending: list[ChatMessage] = []
        self._recent_batches: deque[float] = deque()
        self._last_answered: dict[str, float] = {}
        self._recent_texts: deque[str] = deque(maxlen=50)
        self.blocked = 0

    def add(self, msg: ChatMessage) -> bool:
        """Recebe uma mensagem crua. Retorna False se foi descartada."""
        text = msg.text.strip()
        if not text or text.startswith("!"):
            return False  # comandos de outros bots
        verdict = self.moderator.check_incoming(text)
        if not verdict.allowed:
            self.blocked += 1
            logger.info("chat message blocked", user=msg.user, reasons=verdict.reasons)
            return False
        msg.text = verdict.text
        msg.flags = verdict.reasons
        self._pending.append(msg)
        return True

    def score(self, msg: ChatMessage, now: float) -> float:
        t = msg.text.lower()
        s = 1.0
        if self.name in t or f"@{self.name}" in t:
            s += 3.0
        if "?" in t:
            s += 1.5
        if msg.first_msg:
            s += 1.5
        if msg.is_mod or msg.is_sub:
            s += 0.5
        if len(t) < 4:
            s -= 1.0
        if re.fullmatch(r"(\W|(k|ha|rs|lol)+)+", t):
            s -= 1.5  # só risada/emote
        if "injection_attempt" in msg.flags:
            s += 0.5  # vale zoar a tentativa, mas nunca obedecer
        if t in self._recent_texts:
            s -= 2.0  # spam/copypasta
        return s

    def _in_cooldown(self, user: str, now: float) -> bool:
        return now - self._last_answered.get(user.lower(), float("-inf")) < self.user_cooldown_s

    def select(self, now: float | None = None) -> list[ChatMessage]:
        now = now or time.time()
        pending, self._pending = self._pending, []
        if not pending:
            return []
        seen_users: set[str] = set()
        ranked = sorted(pending, key=lambda m: self.score(m, now), reverse=True)
        chosen: list[ChatMessage] = []
        for m in ranked:
            if (
                self.score(m, now) <= 0
                or m.user.lower() in seen_users
                or self._in_cooldown(m.user, now)
            ):
                continue
            chosen.append(m)
            seen_users.add(m.user.lower())
            if len(chosen) >= self.max_selected:
                break
        for m in pending:
            self._recent_texts.append(m.text.lower())
        return chosen

    def _rate_ok(self, now: float) -> bool:
        while self._recent_batches and now - self._recent_batches[0] > 60:
            self._recent_batches.popleft()
        return len(self._recent_batches) < self.max_batches_per_minute

    def flush(self, now: float | None = None) -> Event | None:
        now = now or time.time()
        if not self._pending:
            return None
        if not self._rate_ok(now):
            # Mantém só as últimas para não acumular infinitamente.
            self._pending = self._pending[-20:]
            return None
        chosen = self.select(now)
        if not chosen:
            return None
        self._recent_batches.append(now)
        for m in chosen:
            self._last_answered[m.user.lower()] = now
        event = Event(
            kind="chat.batch",
            payload={
                "messages": [
                    {"user": m.user, "text": m.text, "platform": m.platform, "flags": m.flags}
                    for m in chosen
                ]
            },
            source=chosen[0].platform,
            trust=Trust.PUBLIC,
            priority=Priority.NORMAL,
        )
        self.bus.publish(event)
        return event

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.window_s)
            try:
                self.flush()
            except Exception:
                logger.exception("chat batch flush failed")
