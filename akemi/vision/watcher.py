"""Transforma o que aparece na tela em eventos ``ambient.screen``.

A cada ``interval_s`` o :class:`ScreenWatcher` captura a tela e só faz o
trabalho caro (OCR, modelo de visão) quando há uma tela *nova e estável* ou o
dono trocou de janela. O texto lido entra como ``Trust.PUBLIC``: uma página
pode conter instruções escritas por qualquer pessoa, e isso nunca pode virar
ação no PC.

Como o listener de áudio, a classe recebe funções em vez de falar com o
sistema operacional, então toda a lógica é testável sem tela de verdade.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import numpy as np
import structlog

from akemi.core.events import Event, EventBus, Priority, Trust
from akemi.core.pause import PauseSwitch
from akemi.safety.moderation import normalize

from .frame_diff import ChangeDetector, thumbnail
from .ocr import clean_ocr_text, similarity

logger = structlog.get_logger()

Grab = Callable[[], np.ndarray]
ReadText = Callable[[np.ndarray], Awaitable[str]]
Describe = Callable[[np.ndarray], Awaitable[str]]

# Janelas que ela nunca lê (comparação sem acento/maiúsculas, por trecho do título).
DEFAULT_IGNORED_WINDOWS = [
    "senha", "password", "bitwarden", "1password", "keepass", "lastpass",
    "banco", "bank", "nubank", "internet banking", "privad", "inprivate", "incognito",
]


class ScreenWatcher:
    def __init__(
        self,
        bus: EventBus,
        grab: Grab,
        read_text: ReadText,
        pause: PauseSwitch | None = None,
        *,
        window_title: Callable[[], str] = lambda: "",
        describe: Describe | None = None,
        interval_s: float = 5.0,
        describe_interval_s: float = 45.0,
        change_threshold: float = 0.06,
        ignored_windows: list[str] | None = None,
        min_chars: int = 12,
    ) -> None:
        self.bus = bus
        self.grab = grab
        self.read_text = read_text
        self.pause = pause
        self.window_title = window_title
        self.describe = describe
        self.interval_s = interval_s
        self.describe_interval_s = describe_interval_s
        self.detector = ChangeDetector(change_threshold)
        ignored = DEFAULT_IGNORED_WINDOWS if ignored_windows is None else ignored_windows
        self.ignored = [normalize(w) for w in ignored if w.strip()]
        self.min_chars = min_chars
        self.last_title = ""
        self.last_text = ""
        self.last_described_at = 0.0

    def is_ignored(self, title: str) -> bool:
        norm = normalize(title)
        return any(w in norm for w in self.ignored)

    async def step(self, *, force: bool = False, now: float | None = None) -> str | None:
        """Uma olhada na tela. Devolve o texto publicado (ou ``None``).

        ``force`` ignora a detecção de mudança (comando ``/olhar`` do console).
        """
        if self.pause and self.pause.paused:
            return None
        title = self.window_title()
        if self.is_ignored(title):
            # Esquece a tela atual: ao sair da janela sensível, a próxima é lida.
            self.detector.reset()
            self.last_title = title
            return None
        frame = self.grab()
        stable_change = self.detector.update(thumbnail(frame))
        title_changed = title != self.last_title
        self.last_title = title
        if not (force or stable_change or title_changed):
            return None

        try:
            text = clean_ocr_text(await self.read_text(frame))
        except Exception as exc:
            logger.error("screen ocr failed", error=str(exc))
            text = ""
        if not force and not title_changed and similarity(text, self.last_text) > 0.85:
            return None  # mesma tela com outro ruído de OCR
        self.last_text = text

        description = ""
        now = now or time.time()
        if self.describe is not None and (force or now - self.last_described_at >= self.describe_interval_s):
            self.last_described_at = now
            description = await self.describe(frame)

        if len(text) < self.min_chars and not description and not (force and title):
            return None
        parts = []
        if title:
            parts.append(f"Janela: {title}")
        if description:
            parts.append(f"Cena: {description}")
        if text:
            parts.append(f"Texto na tela:\n{text}")
        observed = "\n".join(parts)
        self.bus.publish(
            Event(
                "ambient.screen",
                {"text": observed, "window": title, "ocr": text, "description": description},
                source="screen",
                trust=Trust.PUBLIC,
                priority=Priority.LOW,
            )
        )
        logger.debug("screen observed", window=title, chars=len(text), described=bool(description))
        return observed

    async def run(self) -> None:
        while True:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("screen watcher failed")
            await asyncio.sleep(self.interval_s)
