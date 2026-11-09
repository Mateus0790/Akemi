"""Discord: a secretária entra como bot nos seus servidores.

O Discord não deixa bot nenhum ler as suas DMs pessoais, e automatizar a sua
própria conta ("selfbot") é proibido e dá banimento. Então ela entra do jeito
permitido, como um bot (docs/SECRETARIA.md):

- **Num servidor**, quando alguém te marca (@você) ou responde uma mensagem
  sua, ela responde no canal. Quando você mesmo escreve no canal, ela sai de
  cena ali por um tempo.
- **Na DM do bot**, quem escrever para ela deixa recado.
- Com ``DISCORD_PRESENCA``, colocar "Não perturbe" no Discord liga o modo ocupado.

Gateway (WebSocket) + REST, só com ``websockets`` e ``httpx``. As respostas não
marcam ninguém (``allowed_mentions`` vazio): nada de @everyone por ela.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from collections.abc import Callable
from typing import Any

import httpx
import structlog

from . import Mensagem, Secretaria

logger = structlog.get_logger()

GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/Mateus0790/Akemi, 0.2)"

GUILDS, GUILD_PRESENCES, GUILD_MESSAGES = 1 << 0, 1 << 8, 1 << 9
DIRECT_MESSAGES, MESSAGE_CONTENT = 1 << 12, 1 << 15
# Fechou com um destes: tentar de novo não resolve (token errado, intent não liberado).
FATAL = {4004: "token recusado (confira o DISCORD_BOT_TOKEN)",
         4013: "intents inválidos",
         4014: "intents não liberados: ligue MESSAGE CONTENT (e PRESENCE, com DISCORD_PRESENCA) no "
               "Developer Portal, em Bot → Privileged Gateway Intents"}
MENTION_RE = re.compile(r"<@!?(\d+)>")
OTHER_TAGS = ((re.compile(r"<#\d+>"), "#canal"), (re.compile(r"<@&\d+>"), "@cargo"),
              (re.compile(r"<a?:(\w+):\d+>"), r":\1:"))


def display_name(user: dict, member: dict | None = None) -> str:
    return str((member or {}).get("nick") or user.get("global_name") or user.get("username") or "alguém")[:80]


def clean_content(msg: dict) -> str:
    """ "<@123> tá aí?" -> "@Mateus tá aí?"."""
    names = {str(u.get("id")): display_name(u, u.get("member")) for u in msg.get("mentions") or []}
    text = MENTION_RE.sub(lambda m: "@" + names.get(m.group(1), "alguém"), str(msg.get("content") or ""))
    for rx, repl in OTHER_TAGS:
        text = rx.sub(repl, text)
    if not text.strip() and msg.get("attachments"):
        text = "(mandou um arquivo)"
    return text.strip()


class DiscordFatal(RuntimeError):
    pass


class DiscordChats:
    def __init__(
        self,
        secretaria: Secretaria,
        token: str,
        owner_id: str,
        *,
        presence: bool = False,
        client: httpx.AsyncClient | None = None,
        connect: Callable[..., Any] | None = None,
    ) -> None:
        if not token.strip():
            raise ValueError("faltou o DISCORD_BOT_TOKEN")
        self.secretaria = secretaria
        self._token = token.strip()
        self.owner_id = str(owner_id or "").strip()
        self.presence = presence
        self._client = client
        self._connect = connect
        self.bot_id = ""
        self.seq: int | None = None
        self.answered = 0
        if not self.owner_id:
            logger.warning("discord: DISCORD_DONO_ID missing, she only answers the bot's DMs")

    @property
    def intents(self) -> int:
        bits = GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT
        return bits | GUILD_PRESENCES if self.presence else bits

    # ------------------------------------------------------------- mensagens
    def _addressed(self, msg: dict) -> bool:
        """Num servidor: te marcaram, responderam você ou chamaram ela?"""
        mentioned = {str(u.get("id")) for u in msg.get("mentions") or []}
        replied = str(((msg.get("referenced_message") or {}).get("author") or {}).get("id") or "")
        wanted = {self.owner_id, self.bot_id} - {""}
        return bool(mentioned & wanted) or (bool(self.owner_id) and replied == self.owner_id)

    async def on_message(self, msg: dict) -> None:
        author = msg.get("author") or {}
        author_id = str(author.get("id") or "")
        if author.get("bot") or not author_id or author_id == self.bot_id:
            return
        guild = msg.get("guild_id")
        channel = str(msg.get("channel_id") or "")
        name = display_name(author, msg.get("member"))
        if guild:
            contact = Mensagem(name, clean_content(msg), "discord", f"canal:{channel}")
            if author_id == self.owner_id:
                self.secretaria.took_over(contact)  # você está nesse canal: ela sai de cena
                return
            if not self._addressed(msg):
                return
        else:
            if author_id == self.owner_id:
                return  # você na DM do bot: não é recado
            contact = Mensagem(name, clean_content(msg), "discord", author_id)
        reply = await self.secretaria.receive(contact)
        if reply:
            await self.send(channel, reply, reply_to=str(msg.get("id") or ""))

    def on_presence(self, presence: dict) -> None:
        if not self.presence or str((presence.get("user") or {}).get("id") or "") != self.owner_id:
            return
        self.secretaria.set_auto_busy("discord", presence.get("status") == "dnd")

    async def send(self, channel: str, text: str, reply_to: str = "") -> None:
        client = self._client
        if client is None:
            client = self._client = httpx.AsyncClient(timeout=20)
        body: dict = {"content": text[:2000], "allowed_mentions": {"parse": [], "replied_user": False}}
        if reply_to:
            body["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
        try:
            resp = await client.post(f"{API}/channels/{channel}/messages", json=body, headers={
                "Authorization": f"Bot {self._token}", "User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            logger.warning("discord send failed", error=type(exc).__name__)
            return
        if resp.status_code >= 300:
            logger.warning("discord send refused", status=resp.status_code, body=resp.text[:200])
            return
        self.answered += 1

    # --------------------------------------------------------------- gateway
    async def dispatch(self, payload: dict) -> bool:
        """Trata um pacote do Gateway. Devolve False quando é para reconectar."""
        op, data = payload.get("op"), payload.get("d")
        if payload.get("s") is not None:
            self.seq = payload["s"]
        if op in (7, 9):  # RECONNECT / INVALID_SESSION
            return False
        if op != 0 or not isinstance(data, dict):
            return True
        kind = payload.get("t")
        if kind == "READY":
            self.bot_id = str((data.get("user") or {}).get("id") or "")
            logger.info("discord connected", bot=(data.get("user") or {}).get("username"))
        elif kind == "MESSAGE_CREATE":
            await self.on_message(data)
        elif kind == "PRESENCE_UPDATE":
            self.on_presence(data)
        elif kind == "GUILD_CREATE":
            for presence in data.get("presences") or []:
                self.on_presence(presence)
        return True

    async def _heartbeat(self, ws, interval_s: float) -> None:
        await asyncio.sleep(interval_s * random.random())
        while True:
            await ws.send(json.dumps({"op": 1, "d": self.seq}))
            await asyncio.sleep(interval_s)

    async def session(self) -> None:
        """Uma conexão com o Gateway, até cair ou o Discord pedir para reconectar."""
        connect = self._connect
        if connect is None:
            try:
                from websockets.asyncio.client import connect
            except ImportError as exc:  # pragma: no cover - depende do extra
                raise DiscordFatal('falta o websockets: pip install -e ".[discord]"') from exc
        async with connect(GATEWAY, max_size=2**23) as ws:
            hello = json.loads(await ws.recv())
            interval = float((hello.get("d") or {}).get("heartbeat_interval", 41250)) / 1000
            await ws.send(json.dumps({"op": 2, "d": {
                "token": self._token, "intents": self.intents,
                "properties": {"os": "windows", "browser": "akemi", "device": "akemi"},
            }}))
            beat = asyncio.create_task(self._heartbeat(ws, interval))
            try:
                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(payload, dict) and not await self.dispatch(payload):
                        return
            except Exception as exc:
                code = getattr(getattr(exc, "rcvd", None), "code", None)
                if code in FATAL:
                    raise DiscordFatal(FATAL[code]) from None
                raise
            finally:
                beat.cancel()
            code = getattr(ws, "close_code", None)
            if code in FATAL:
                raise DiscordFatal(FATAL[code])

    async def run(self) -> None:
        """Fica conectado (supervisionado). Token errado ou intent faltando: desliga e avisa no log."""
        while True:
            try:
                await self.session()
            except DiscordFatal as exc:
                logger.error("discord off", reason=str(exc))
                if "discord" in self.secretaria.canais:
                    self.secretaria.canais.remove("discord")
                return
            await asyncio.sleep(1 + random.random() * 4)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
