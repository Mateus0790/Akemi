"""Provedor offline para testes e para rodar o console sem API key."""

from __future__ import annotations

import json
from typing import Callable

from .base import LLMProvider, Message

Responder = Callable[[list[Message], bool], str]


def _default_responder(messages: list[Message], json_mode: bool) -> str:
    last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
    if json_mode:
        return json.dumps(
            {
                "decision": "comment",
                "reason": "echo provider",
                "pensamento": "(eco) pensando na vida",
                "humor": "curiosa",
                "falar": "",
            },
            ensure_ascii=False,
        )
    lines = [ln for ln in last_user.strip().splitlines() if ln.strip() and not ln.startswith("<")]
    if not lines:
        return "..."
    return f"(modo eco) Ouvi: {lines[-1][:200]}"


class EchoProvider(LLMProvider):
    name = "echo"

    def __init__(self, responder: Responder | None = None) -> None:
        self.responder = responder or _default_responder
        self.calls: list[tuple[str, list[Message]]] = []

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 300,
        temperature: float = 0.8,
        json_mode: bool = False,
    ) -> str:
        self.calls.append((model, list(messages)))
        return self.responder(messages, json_mode)
