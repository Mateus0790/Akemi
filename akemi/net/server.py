"""Núcleo da Akemi como servidor (24h, ex.: Docker no ZimaOS).

A mente, a memória, o chat da live e o pensamento ocioso rodam aqui. O PC do
dono conecta como *corpo* (``akemi-corpo``): manda o que ouve e vê, e recebe o
que ela deve falar. Sem corpo conectado ela continua pensando e respondendo no
chat; só não tem voz.

Segurança: só quem manda o ``AKEMI_TOKEN`` certo vira corpo, e só o corpo
autenticado pode falar como dono (Trust.OWNER). O que o corpo vê ou ouve
continua entrando como Trust.PUBLIC.
"""

from __future__ import annotations

import asyncio
import hmac
import itertools
import json
import sys
from http import HTTPStatus
from typing import Any

import structlog

from akemi.actions.desktop import PC_ACTIONS, register_desktop_actions
from akemi.actions.registry import ActionRegistry
from akemi.core.config import Settings
from akemi.core.events import Event, Priority, Trust
from akemi.core.orchestrator import Akemi
from akemi.tts.base import TTSEngine

from . import protocol as proto

logger = structlog.get_logger()

MIN_TOKEN_LEN = 16


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


class AkemiServer:
    def __init__(self, akemi: Akemi, link: BodyLink, token: str) -> None:
        if len(token) < MIN_TOKEN_LEN:
            raise ValueError(f"AKEMI_TOKEN precisa de pelo menos {MIN_TOKEN_LEN} caracteres")
        self.akemi = akemi
        self.link = link
        self._token = token.encode()
        akemi.bus.subscribe("speech.finished", self._on_speech_finished)

    # ------------------------------------------------------------- eventos
    async def _on_speech_finished(self, event: Event) -> None:
        # Ela falou com você: o corpo abre a janela para responder sem o nome.
        if event.payload.get("status") == "done" and event.payload.get("origin_trust", 0) >= Trust.SYSTEM:
            await self.link.send("exchange")

    def _authorized(self, msg: dict) -> bool:
        token = msg.get("token")
        return (
            msg.get("type") == "hello"
            and isinstance(token, str)
            and hmac.compare_digest(token.encode(), self._token)
        )

    async def handle(self, msg: dict) -> None:
        kind, akemi = msg["type"], self.akemi
        if kind == "owner_message":
            text = msg.get("text", "").strip()
            if text:
                akemi.owner_say(text, source="corpo")
        elif kind == "owner_speech_started":
            akemi.bus.publish(
                Event("owner.speech_started", source="corpo", trust=Trust.OWNER, priority=Priority.URGENT)
            )
        elif kind == "ambient":
            text = msg.get("text", "").strip()
            if msg.get("kind") in proto.AMBIENT_KINDS and text:
                akemi.observe(msg["kind"], text)
        elif kind == "spoken":
            self.link.resolve(msg.get("id"), str(msg.get("status", "done")))
        elif kind == "action_result":
            self.link.resolve_action(msg)
        elif kind in ("pause", "resume"):
            if kind == "pause":
                akemi.pause.pause("corpo")
            else:
                akemi.pause.resume()
            await self.link.send("paused", paused=akemi.pause.paused)
        else:
            logger.warning("unknown message from body", type=kind)

    # ---------------------------------------------------------- websocket
    async def handler(self, conn: Any) -> None:
        try:
            first = proto.decode(await asyncio.wait_for(conn.recv(), 10))
        except Exception:  # timeout, JSON inválido ou conexão fechada
            await conn.close(proto.CLOSE_UNAUTHORIZED, "esperava hello")
            return
        if not self._authorized(first):
            logger.warning("body rejected: bad token", remote=str(conn.remote_address))
            await conn.close(proto.CLOSE_UNAUTHORIZED, "token inválido")
            return
        await self.link.attach(conn)
        logger.info("body connected", remote=str(conn.remote_address))
        await self.link.send("welcome", paused=self.akemi.pause.paused, owner=self.akemi.settings.owner_name)
        try:
            async for raw in conn:
                try:
                    await self.handle(proto.decode(raw))
                except proto.ProtocolError as exc:
                    logger.warning("bad message from body", error=str(exc))
        except Exception as exc:  # conexão caiu
            logger.info("body connection ended", error=str(exc))
        finally:
            self.link.detach(conn)
            logger.info("body disconnected")

    def health(self) -> dict:
        state = self.akemi.state
        return {
            "ok": True,
            "corpo_conectado": self.link.connected,
            "pausada": self.akemi.pause.paused,
            "humor": state.mood,
        }

    def process_request(self, conn: Any, request: Any):
        """``GET /saude`` responde em HTTP puro (healthcheck do Docker)."""
        if request.path.rstrip("/") == "/saude":
            return conn.respond(HTTPStatus.OK, json.dumps(self.health(), ensure_ascii=False) + "\n")
        return None

    async def serve(self, host: str, port: int):
        from websockets.asyncio.server import serve

        return await serve(self.handler, host, port, process_request=self.process_request)


def register_remote_actions(registry: ActionRegistry, link: BodyLink) -> None:
    """Registra as ações do PC no núcleo, mas executando no corpo.

    Nome, descrição, exemplo e nível de permissão são os mesmos do modo local:
    o núcleo continua checando quem pediu e pegando o "sim"; o corpo só executa.
    """
    register_desktop_actions(registry)  # só pelos metadados; as funções são trocadas
    for name in PC_ACTIONS:
        action = registry.get(name)
        if action is None:
            continue

        def make(name: str, original):
            async def remote(**kwargs):
                result = await link.request_action(name, kwargs)
                if result.get("status") != "executed":
                    raise ValueError(result.get("detail") or "o PC não conseguiu")
                return result.get("value")

            remote.__wrapped__ = original  # o registro filtra os argumentos pela assinatura original
            return remote

        action.fn = make(name, action.fn)


def build(settings: Settings, **akemi_kwargs) -> AkemiServer:
    # No servidor não há microfone, tela nem placa de som: isso é do corpo.
    settings.mic_enabled = settings.screen_enabled = settings.system_audio_enabled = False
    link = BodyLink()
    akemi_kwargs.setdefault("desktop_actions", False)
    akemi = Akemi(settings, tts=RemoteVoice(link), **akemi_kwargs)
    register_remote_actions(akemi.actions, link)
    return AkemiServer(akemi, link, settings.akemi_token)


async def run(settings: Settings) -> None:
    server = build(settings)
    await server.akemi.start()
    ws = await server.serve(settings.server_host, settings.server_port)
    logger.info("akemi core listening", host=settings.server_host, port=settings.server_port)
    try:
        await ws.serve_forever()
    finally:
        ws.close()
        await server.akemi.stop()


def main() -> None:
    from akemi.core.logging_setup import configure_logging

    settings = Settings()
    configure_logging(settings.log_level, settings.log_json)
    if len(settings.akemi_token) < MIN_TOKEN_LEN:
        print(
            "Defina AKEMI_TOKEN (pelo menos 16 caracteres) no .env ou no Docker. Gere um com:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(24))"',
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
