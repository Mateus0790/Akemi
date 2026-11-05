"""Cérebro reserva: se a nuvem cair, ela troca sozinha para outro cérebro.

A Akemi não é o modelo que roda por baixo (Projeto Capricórnio), então perder
o NIM não pode deixar ela muda. O :class:`FallbackProvider` tenta os cérebros
em ordem (ex.: NIM → Ollama local) e:

- quando o atual falha (rede, HTTP 5xx, timeout), passa para o próximo na hora,
  no mesmo pedido;
- o que falhou fica "de castigo" por ``retry_after_s``; depois disso ela tenta
  voltar para ele sozinha (o primeiro da lista é o preferido);
- a cada troca chama ``on_switch(de, para)``, para ela poder contar ao dono.

Cada cérebro tem seus próprios nomes de modelo: o modelo pedido (o rápido ou o
profundo do ``.env``) é traduzido pelo ``model_map`` de cada um.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import AsyncIterator

import structlog

from .base import LLMProvider, Message, ProviderError

logger = structlog.get_logger()


@dataclass
class Brain:
    provider: LLMProvider
    name: str
    # modelo pedido -> modelo deste cérebro ("*" = qualquer outro). Vazio = o mesmo nome.
    model_map: dict[str, str] = field(default_factory=dict)
    failed_at: float = 0.0
    last_error: str = ""

    def model_for(self, requested: str) -> str:
        return self.model_map.get(requested) or self.model_map.get("*") or requested


class FallbackProvider(LLMProvider):
    name = "fallback"

    def __init__(
        self,
        brains: list[Brain],
        *,
        retry_after_s: float = 120.0,
        on_switch: Callable[[str, str, str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not brains:
            raise ValueError("precisa de pelo menos um cérebro")
        self.brains = brains
        self.retry_after_s = retry_after_s
        self.on_switch = on_switch
        self._clock = clock
        self.active = brains[0].name

    # ------------------------------------------------------------ escolha
    def _order(self) -> list[Brain]:
        """Os saudáveis primeiro (na ordem de preferência), depois os de castigo."""
        now = self._clock()
        healthy = [b for b in self.brains if not b.failed_at or now - b.failed_at >= self.retry_after_s]
        resting = sorted((b for b in self.brains if b not in healthy), key=lambda b: b.failed_at)
        return healthy + resting

    def _mark(self, brain: Brain, error: str | None) -> None:
        if error is None:
            brain.failed_at, brain.last_error = 0.0, ""
            if brain.name != self.active:
                previous, self.active = self.active, brain.name
                reason = next((b.last_error for b in self.brains if b.name == previous), "")
                logger.warning("brain switched", de=previous, para=brain.name, motivo=reason)
                if self.on_switch:
                    try:
                        self.on_switch(previous, brain.name, reason)
                    except Exception:  # pragma: no cover - aviso nunca derruba a resposta
                        logger.exception("on_switch failed")
        else:
            brain.failed_at, brain.last_error = self._clock(), error[:200]
            logger.warning("brain failed", brain=brain.name, error=brain.last_error)

    @property
    def primary(self) -> str:
        return self.brains[0].name

    def status(self) -> dict:
        now = self._clock()
        return {
            "ativo": self.active,
            "preferido": self.primary,
            "cerebros": [
                {
                    "nome": b.name,
                    "ok": not b.failed_at,
                    "erro": b.last_error,
                    "volta_em_s": max(0, int(self.retry_after_s - (now - b.failed_at))) if b.failed_at else 0,
                }
                for b in self.brains
            ],
        }

    # ------------------------------------------------------------ chamadas
    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 300,
        temperature: float = 0.8,
        json_mode: bool = False,
    ) -> str:
        errors = []
        for brain in self._order():
            try:
                out = await brain.provider.complete(
                    messages, model=brain.model_for(model), max_tokens=max_tokens,
                    temperature=temperature, json_mode=json_mode,
                )
            except Exception as exc:  # ProviderError, rede, timeout...
                self._mark(brain, str(exc) or type(exc).__name__)
                errors.append(f"{brain.name}: {exc}")
                continue
            self._mark(brain, None)
            return out
        raise ProviderError("todos os cérebros falharam: " + " | ".join(errors))

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int = 300,
        temperature: float = 0.8,
    ) -> AsyncIterator[str]:
        errors = []
        for brain in self._order():
            started = False
            try:
                async for chunk in brain.provider.stream(
                    messages, model=brain.model_for(model), max_tokens=max_tokens, temperature=temperature
                ):
                    if not started:
                        started = True
                        self._mark(brain, None)
                    yield chunk
            except Exception as exc:
                if started:  # já falou metade: trocar agora repetiria a frase
                    self._mark(brain, str(exc) or type(exc).__name__)
                    raise ProviderError(f"{brain.name} caiu no meio da resposta: {exc}") from exc
                self._mark(brain, str(exc) or type(exc).__name__)
                errors.append(f"{brain.name}: {exc}")
                continue
            if not started:
                self._mark(brain, None)
            return
        raise ProviderError("todos os cérebros falharam: " + " | ".join(errors))

    async def aclose(self) -> None:
        seen: set[int] = set()
        for b in self.brains:
            if id(b.provider) not in seen:
                seen.add(id(b.provider))
                await b.provider.aclose()
