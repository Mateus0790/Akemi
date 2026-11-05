"""A conexão com um corpo (PC ou celular): falas com confirmação e ações.

Usada pelo núcleo para o corpo principal (``akemi-corpo``) e para o celular.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

import structlog

from akemi.tts.base import TTSEngine

from . import protocol as proto

logger = structlog.get_logger()


class BodyLink:
    """A conexão com o corpo (no máximo um; um corpo novo substitui o antigo)."""

    def __init__(self) -> None:
        self.conn: Any = None
        self._pending: dict[int, asyncio.Future[str]] = {}
        self._actions: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)

    @property
    def connected(self) -> bool:
        return self.conn is not None

    @property
    def busy(self) -> bool:
        """Há fala enviada ao corpo ainda sem confirmação."""
        return bool(self._pending)

    async def attach(self, conn: Any) -> None:
        old, self.conn = self.conn, conn
        if old is not None and old is not conn:
            self._fail_pending()
            await old.close(proto.CLOSE_REPLACED, "outro corpo conectou")

    def detach(self, conn: Any) -> None:
        if self.conn is conn:
            self.conn = None
            self._fail_pending()

    def _fail_pending(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result("disconnected")
        self._pending.clear()
        for fut in self._actions.values():
            if not fut.done():
                fut.set_result({"status": "disconnected"})

    async def send(self, type_: str, **fields: Any) -> bool:
        conn = self.conn
        if conn is None:
            return False
        try:
            await conn.send(proto.encode(type_, **fields))
            return True
        except Exception as exc:  # conexão caiu no meio do envio
            logger.warning("send to body failed", error=str(exc))
            return False

    async def say(self, text: str) -> asyncio.Future[str] | None:
        """Pede ao corpo para falar; o futuro resolve quando ele terminar."""
        sid = next(self._ids)
        fut: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[sid] = fut
        if not await self.send("say", id=sid, text=text):
            self._pending.pop(sid, None)
            return None
        return fut

    def resolve(self, sid: Any, status: str) -> None:
        fut = self._pending.pop(sid, None) if isinstance(sid, int) else None
        if fut is not None and not fut.done():
            fut.set_result(status)

    async def request_action(self, name: str, args: dict, timeout: float = 20.0) -> dict:
        """Pede ao corpo para executar uma ação do PC e espera o resultado."""
        aid = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._actions[aid] = fut
        try:
            if not await self.send("action", id=aid, name=name, args=args):
                raise ValueError("o PC não está conectado")
            result = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            raise ValueError("o PC não respondeu") from exc
        finally:
            self._actions.pop(aid, None)
        if result.get("status") == "disconnected":
            raise ValueError("o PC desconectou")
        return result

    def resolve_action(self, msg: dict) -> None:
        fut = self._actions.get(msg.get("id")) if isinstance(msg.get("id"), int) else None
        if fut is not None and not fut.done():
            fut.set_result(msg)


class RemoteVoice(TTSEngine):
    """TTS do núcleo: a voz de verdade toca no corpo.

    O ``Speaker`` do núcleo continua sendo o único caminho para falar (com a
    moderação de saída), só que o "alto-falante" está do outro lado da rede.
    """

    def __init__(self, link: BodyLink, *, chars_per_second: float = 8.0, min_timeout_s: float = 15.0) -> None:
        self.link = link
        self.cps = chars_per_second
        self.min_timeout_s = min_timeout_s

    async def speak(self, text: str) -> None:
        fut = await self.link.say(text)
        if fut is None:
            logger.info("no body connected; speech not played", text=text)
            return
        timeout = max(self.min_timeout_s, len(text) / self.cps)
        try:
            status = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            logger.warning("body did not confirm speech", text=text)
            return
        except asyncio.CancelledError:
            await self.link.send("stop")  # interrompida: o corpo para na hora
            raise
        if status == "error":
            logger.warning("body failed to speak", text=text)

    async def stop(self) -> None:
        if self.link.busy:  # só incomoda o corpo se ele estiver falando algo nosso
            await self.link.send("stop")
