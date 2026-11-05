"""O celular como acompanhante: a mesma Akemi, em outro aparelho.

O chip (memória, jeito, estado) continua no núcleo, no PC ou no servidor. O
celular só empresta tela, microfone e alto-falante, igual ao corpo do PC, mas
sem tirar o PC do lugar: os dois ficam conectados ao mesmo tempo.

Ela responde **onde você falou por último**: mandou mensagem pelo celular, a
resposta sai no celular. Falou com ela no PC (voz ou terminal), volta a sair
no PC. O celular recebe também uma cópia de tudo o que ela diz (``fala``),
para o histórico e para as notificações (lembretes, iniciativas, avisos).

Protocolo (JSON sobre WebSocket, mesma porta do núcleo):

Celular → núcleo
    ``hello``          {token, version, corpo: {nome, tipo: "celular"}}
    ``owner_message``  {text}           você digitou/falou no celular (Trust.OWNER)
    ``owner_speech_started`` {}         você começou a falar: ela para
    ``spoken``         {id, status}     terminou de falar o ``say`` ``id``
    ``pause`` / ``resume`` {}

Núcleo → celular
    ``welcome``        {paused, owner, nome, historico: [{de, texto}]}
    ``say``            {id, text}       fale isto (você está no celular)
    ``fala``           {text}           ela disse isto em outro lugar (só mostra)
    ``stop``           {}               pare de falar
    ``paused``         {paused}

Segurança: só entra quem manda o ``AKEMI_TOKEN``. O celular fala como dono,
então pode pedir ações no PC (que continuam pedindo "sim" quando precisam).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import structlog

from akemi.core.events import Event, Priority, Trust
from akemi.tts.base import TTSEngine

from . import protocol as proto
from .link import BodyLink, RemoteVoice

if TYPE_CHECKING:
    from akemi.core.orchestrator import Akemi

logger = structlog.get_logger()

SOURCE = "celular"
KIND = "celular"
HISTORY_ITEMS = 20


def is_companion_hello(msg: dict) -> bool:
    corpo = msg.get("corpo")
    return isinstance(corpo, dict) and corpo.get("tipo") == KIND


class FocusVoice(TTSEngine):
    """Escolhe o alto-falante: o celular quando você está nele, senão a voz de sempre."""

    def __init__(self, engine: TTSEngine, hub: "CompanionHub") -> None:
        self.engine = engine
        self.hub = hub
        self.phone = RemoteVoice(hub.link)
        self._using: TTSEngine = engine

    async def warmup(self) -> None:
        warm = getattr(self.engine, "warmup", None)
        if warm is not None:
            await warm()

    async def speak(self, text: str) -> None:
        self._using = self.phone if self.hub.focused else self.engine
        await self._using.speak(text)

    async def stop(self) -> None:
        await self._using.stop()


class CompanionHub:
    def __init__(self, akemi: "Akemi", token: str) -> None:
        self.akemi = akemi
        self.link = BodyLink()
        self._token = token.encode()
        self.focused = False  # a última conversa veio do celular
        self.name = ""
        akemi.bus.subscribe("owner.message", self._on_owner_message)
        akemi.bus.subscribe("speech.started", self._mirror)

    @property
    def connected(self) -> bool:
        return self.link.connected

    def install(self) -> None:
        """Põe a escolha de alto-falante na frente da voz dela (chamar depois do start)."""
        speaker = self.akemi.speaker
        if not isinstance(speaker.engine, FocusVoice):
            speaker.engine = FocusVoice(speaker.engine, self)

    # ------------------------------------------------------------- eventos
    def _on_owner_message(self, event: Event) -> None:
        # Falou com ela por outro lugar (voz no PC, terminal): a resposta volta para lá.
        self.focused = event.source == SOURCE and self.connected

    async def _mirror(self, event: Event) -> None:
        if self.connected and not self.focused and event.text:
            await self.link.send("fala", text=event.text)

    def history(self) -> list[dict]:
        items = []
        for m in list(self.akemi.mind.history)[-HISTORY_ITEMS:]:
            text = m.content.strip()
            if text and not text.startswith("[[acao:"):
                items.append({"de": "voce" if m.role == "user" else "akemi", "texto": text})
        return items

    # ---------------------------------------------------------- mensagens
    def authorized(self, msg: dict) -> bool:
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
                self.focused = True
                akemi.owner_say(text, source=SOURCE)
        elif kind == "owner_speech_started":
            akemi.bus.publish(
                Event("owner.speech_started", source=SOURCE, trust=Trust.OWNER, priority=Priority.URGENT)
            )
        elif kind == "spoken":
            self.link.resolve(msg.get("id"), str(msg.get("status", "done")))
        elif kind in ("pause", "resume"):
            if kind == "pause":
                akemi.pause.pause(SOURCE)
            else:
                akemi.pause.resume()
            await self.link.send("paused", paused=akemi.pause.paused)
        else:
            logger.warning("unknown message from phone", type=kind)

    async def serve_conn(self, conn: Any, hello: dict) -> None:
        """Atende um celular já autenticado (o ``hello`` já foi lido e conferido)."""
        await self.link.attach(conn)
        name, _ = proto.body_info(hello.get("corpo"))
        self.name = name
        logger.info("phone connected", phone=name)
        await self.link.send(
            "welcome", paused=self.akemi.pause.paused, owner=self.akemi.settings.owner_name,
            nome=self.akemi.mind.persona.name, historico=self.history(),
        )
        owner = self.akemi.settings.owner_name
        self.akemi.mind.ambient.append((time.time(), "celular", f"{owner} abriu o app no celular ({name})"))
        try:
            async for raw in conn:
                try:
                    await self.handle(proto.decode(raw))
                except proto.ProtocolError as exc:
                    logger.warning("bad message from phone", error=str(exc))
        except Exception as exc:  # conexão caiu (celular bloqueou, saiu do Wi-Fi...)
            logger.info("phone connection ended", error=str(exc))
        finally:
            self.link.detach(conn)
            if not self.link.connected:
                self.focused = False
            logger.info("phone disconnected", phone=name)

    # -------------------------------------------- servidor só do celular
    async def handler(self, conn: Any) -> None:
        """Usado no modo local (``akemi --celular``): aqui só entra celular."""
        try:
            first = proto.decode(await asyncio.wait_for(conn.recv(), 10))
        except Exception:
            await conn.close(proto.CLOSE_UNAUTHORIZED, "esperava hello")
            return
        if not self.authorized(first):
            logger.warning("phone rejected: bad token", remote=str(conn.remote_address))
            await conn.close(proto.CLOSE_UNAUTHORIZED, "token inválido")
            return
        if not is_companion_hello(first):
            await conn.close(proto.CLOSE_BAD_MESSAGE, "no modo local só o celular conecta")
            return
        await self.serve_conn(conn, first)

    def status(self) -> dict:
        return {"conectado": self.connected, "nome": self.name if self.connected else "", "em_foco": self.focused}

    def process_request(self, conn: Any, request: Any):
        if request.path.rstrip("/") == "/saude":
            body = {"ok": True, "celular": self.status(), "pausada": self.akemi.pause.paused}
            return conn.respond(HTTPStatus.OK, json.dumps(body, ensure_ascii=False) + "\n")
        return None

    async def serve(self, host: str, port: int):
        from websockets.asyncio.server import serve

        return await serve(self.handler, host, port, process_request=self.process_request)
