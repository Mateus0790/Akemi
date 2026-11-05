"""Autocura: ela percebe quando uma parte dela quebra e se conserta sozinha.

Três peças:

- :class:`Health`: o "painel interno". Cada parte (cérebro, voz, chat, tela...)
  se reporta como ok ou com problema. Quando o estado muda, publica
  ``health.changed`` no barramento, e a mente conta ao dono ("minha voz caiu,
  tô me consertando" / "pronto, voltou").
- :func:`supervise`: roda uma tarefa de fundo e, se ela morrer com erro,
  reinicia com espera crescente (1 s, 2 s, 4 s... até 60 s). Um bug num
  sentido nunca derruba a Akemi inteira nem fica morto para sempre.
- :class:`ResilientTTS`: se a voz falhar várias vezes seguidas, ela passa a
  escrever no terminal (nada do que ia falar se perde) e de tempos em tempos
  tenta a voz de novo.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog

from akemi.core.events import Event, EventBus, Priority, Trust
from akemi.tts.base import ConsoleTTS, TTSEngine

logger = structlog.get_logger()


@dataclass
class Component:
    name: str
    label: str  # como ela chama essa parte em voz alta ("voz", "cérebro")
    ok: bool = True
    detail: str = ""
    since: float = field(default_factory=time.time)
    failures: int = 0


class Health:
    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus
        self.components: dict[str, Component] = {}

    def report(self, name: str, ok: bool, detail: str = "", *, label: str = "") -> bool:
        """Atualiza uma parte. Devolve True se o estado mudou (ok ↔ problema)."""
        comp = self.components.get(name)
        if comp is None:
            comp = self.components[name] = Component(name, label or name)
            changed = not ok  # parte nova só vira notícia se já nasce com problema
        else:
            changed = comp.ok != ok
        if label:
            comp.label = label
        comp.detail = detail[:200]
        if not ok:
            comp.failures += 1
        if changed:
            comp.ok, comp.since = ok, time.time()
            (logger.info if ok else logger.warning)("health changed", part=name, ok=ok, detail=comp.detail)
            if self.bus is not None:
                self.bus.publish(Event(
                    "health.changed",
                    {"parte": name, "nome": comp.label, "ok": ok, "detalhe": comp.detail},
                    source="health", trust=Trust.SYSTEM, priority=Priority.LOW,
                ))
        return changed

    @property
    def problems(self) -> list[Component]:
        return [c for c in self.components.values() if not c.ok]

    def describe_for_prompt(self) -> str:
        """Só aparece no prompt quando algo não vai bem (ela sabe como está por dentro)."""
        if not self.problems:
            return ""
        parts = "; ".join(f"{c.label}: {c.detail or 'com problema'}" for c in self.problems)
        return f"Como você está por dentro (autocura em andamento): {parts}."

    def snapshot(self) -> dict:
        return {
            c.name: {"ok": c.ok, "detalhe": c.detail, "falhas": c.failures, "desde": int(c.since)}
            for c in self.components.values()
        }

    def describe(self) -> str:
        if not self.components:
            return "  tudo funcionando (nenhum problema até agora)"
        lines = []
        for c in self.components.values():
            mark = "ok" if c.ok else "PROBLEMA"
            extra = f" ({c.detail})" if c.detail else ""
            lines.append(f"  {c.label}: {mark}{extra}" + (f", {c.failures} falha(s)" if c.failures else ""))
        return "\n".join(lines)


async def supervise(
    name: str,
    factory: Callable[[], Awaitable[None]],
    health: Health | None = None,
    *,
    label: str = "",
    max_backoff_s: float = 60.0,
    stable_after_s: float = 60.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Roda ``factory()`` e reinicia se ela morrer com erro. Cancelar para de vez."""
    delay = 1.0
    while True:
        started = time.monotonic()
        try:
            await factory()
            return  # terminou normalmente: nada a reiniciar
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if time.monotonic() - started >= stable_after_s:
                delay = 1.0  # rodou bem por um tempo: o erro é novo, recomeça rápido
            logger.exception("background task crashed, restarting", task=name, retry_in_s=delay)
            if health is not None:
                health.report(name, False, f"{type(exc).__name__}: {exc}", label=label or name)
        await sleep(delay)
        delay = min(delay * 2, max_backoff_s)
        if health is not None:
            health.report(name, True, "reiniciada", label=label or name)


class ResilientTTS(TTSEngine):
    """Voz que se recupera sozinha.

    Depois de ``max_failures`` erros seguidos, passa a usar o ``fallback``
    (terminal). A cada ``retry_after_s`` tenta a voz de verdade de novo; se
    der certo, volta a falar. Uma frase que falha na voz sai no reserva, então
    nada se perde.
    """

    def __init__(
        self,
        engine: TTSEngine,
        *,
        fallback: TTSEngine | None = None,
        max_failures: int = 3,
        retry_after_s: float = 60.0,
        on_health: Callable[[bool, str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.engine = engine
        self.fallback = fallback or ConsoleTTS()
        self.max_failures = max_failures
        self.retry_after_s = retry_after_s
        self.on_health = on_health
        self._clock = clock
        self.failures = 0
        self.degraded_at: float | None = None
        self._using: TTSEngine = engine

    @property
    def degraded(self) -> bool:
        return self.degraded_at is not None

    async def warmup(self) -> None:
        warm = getattr(self.engine, "warmup", None)
        if warm is not None:
            try:
                await warm()
            except Exception as exc:
                self._degrade(f"não carregou: {exc}")

    def _degrade(self, why: str) -> None:
        if self.degraded_at is None:
            logger.error("voice degraded, using fallback", error=why)
            if self.on_health:
                self.on_health(False, why)
        self.degraded_at = self._clock()

    def _recover(self) -> None:
        if self.degraded_at is not None:
            logger.info("voice recovered")
            self.degraded_at = None
            if self.on_health:
                self.on_health(True, "voltou")
        self.failures = 0

    async def speak(self, text: str) -> None:
        if self.degraded and self._clock() - self.degraded_at < self.retry_after_s:
            self._using = self.fallback
            await self.fallback.speak(text)
            return
        self._using = self.engine
        try:
            await self.engine.speak(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failures += 1
            logger.warning("tts error", error=str(exc), failures=self.failures)
            if self.degraded or self.failures >= self.max_failures:
                self._degrade(f"{type(exc).__name__}: {exc}")
            self._using = self.fallback
            await self.fallback.speak(text)  # a frase não se perde
            return
        self._recover()

    async def stop(self) -> None:
        await self._using.stop()
