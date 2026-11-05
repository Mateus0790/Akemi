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

from . import protocol as proto
from .companion import CompanionHub, is_companion_hello
from .link import BodyLink, RemoteVoice

__all__ = ["AkemiServer", "BodyLink", "RemoteVoice", "build", "register_remote_actions"]

logger = structlog.get_logger()

MIN_TOKEN_LEN = 16


class AkemiServer:
    def __init__(self, akemi: Akemi, link: BodyLink, token: str) -> None:
        if len(token) < MIN_TOKEN_LEN:
            raise ValueError(f"AKEMI_TOKEN precisa de pelo menos {MIN_TOKEN_LEN} caracteres")
        self.akemi = akemi
        self.link = link
        self._token = token.encode()
        self.body_name = ""
        # O celular conecta na mesma porta, ao lado do PC (não substitui o corpo).
        self.phone = CompanionHub(akemi, token)
        self.phone.install()
        akemi.bus.subscribe("speech.finished", self._on_speech_finished)

    # ------------------------------------------------------------- eventos
    async def _on_speech_finished(self, event: Event) -> None:
        # Ela falou com você: o corpo abre a janela para responder sem o nome.
        if self.phone.focused:
            return  # ela falou no celular, não no PC
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
        elif kind == "evolucao":
            await self.link.send("evolucao", texto=await self._evolution(msg))
        else:
            logger.warning("unknown message from body", type=kind)

    async def _evolution(self, msg: dict) -> str:
        """Pedido do dono (corpo autenticado) sobre a autoevolução."""
        from akemi.brain.self_improve import EvolutionError

        action, akemi = msg.get("acao"), self.akemi
        if action not in proto.EVOLUTION_ACTIONS:
            return "ação inválida"
        try:
            if action == "listar":
                return akemi.evolution.describe()
            if action == "evoluir":
                made = await akemi.evolve(force=True)
                return "\n".join(p.describe() for p in made) or "Nada para mudar agora."
            pid = msg.get("id")
            if not isinstance(pid, int):
                return "informe o número da proposta"
            return akemi.decide_evolution(action, pid).describe()
        except (EvolutionError, ValueError) as exc:
            return f"Não deu: {exc}"

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
        if is_companion_hello(first):
            await self.phone.serve_conn(conn, first)
            return
        await self.link.attach(conn)
        name, senses = proto.body_info(first.get("corpo"))
        self.body_name = name
        logger.info("body connected", remote=str(conn.remote_address), body=name, senses=senses)
        await self.link.send("welcome", paused=self.akemi.pause.paused, owner=self.akemi.settings.owner_name)
        self.akemi.bus.publish(
            Event("body.connected", {"nome": name, "sentidos": senses}, source="corpo", trust=Trust.SYSTEM)
        )
        try:
            async for raw in conn:
                try:
                    await self.handle(proto.decode(raw))
                except proto.ProtocolError as exc:
                    logger.warning("bad message from body", error=str(exc))
        except Exception as exc:  # conexão caiu
            logger.info("body connection ended", error=str(exc))
        finally:
            if self.link.conn is conn:  # um corpo substituído não "desliga" o novo
                self.akemi.bus.publish(Event("body.disconnected", source="corpo", trust=Trust.SYSTEM))
            self.link.detach(conn)
            logger.info("body disconnected")

    def health(self) -> dict:
        state = self.akemi.state
        return {
            "ok": True,
            "corpo_conectado": self.link.connected,
            "corpo": self.body_name if self.link.connected else "",
            "pausada": self.akemi.pause.paused,
            "humor": state.mood,
            "celular": self.phone.status(),
            "autocura": self.akemi.health_report(),
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
    akemi.local_body = False  # o corpo de verdade se anuncia no hello
    register_remote_actions(akemi.actions, link)
    akemi.allow_initiative()  # midia/volume só existem depois do registro remoto
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
