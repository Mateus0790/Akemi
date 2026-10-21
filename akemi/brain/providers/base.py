"""Interface comum para provedores de LLM."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal

Role = Literal["system", "user", "assistant"]


@dataclass(slots=True)
class Message:
    role: Role
    content: str
    images: list[str] = field(default_factory=list)  # JPEG em base64 (modelos de visão)


class ProviderError(RuntimeError):
    pass


class LLMProvider(abc.ABC):
    """Todo provedor implementa ``complete``; ``stream`` tem fallback padrão."""

    name: str = "base"

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 300,
        temperature: float = 0.8,
        json_mode: bool = False,
    ) -> str: ...

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 300,
        temperature: float = 0.8,
    ) -> AsyncIterator[str]:
        """Gera pedaços de texto. Provedores que suportam SSE sobrescrevem isso
        para que o TTS comece a falar antes da resposta terminar."""
        yield await self.complete(
            messages, model=model, max_tokens=max_tokens, temperature=temperature
        )

    async def aclose(self) -> None:  # pragma: no cover - trivial
        return None
