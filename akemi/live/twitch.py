"""Cliente mínimo do chat da Twitch via IRC (sem dependências externas).

Sem ``TWITCH_OAUTH_TOKEN`` conecta em modo anônimo (só leitura), que é o
suficiente para a Akemi responder por voz na live.
"""

from __future__ import annotations

import asyncio
import random
import ssl
from dataclasses import dataclass

import structlog

from .chat import ChatBatcher, ChatMessage

logger = structlog.get_logger()

HOST, PORT = "irc.chat.twitch.tv", 6697


@dataclass
class IrcLine:
    tags: dict[str, str]
    prefix: str
    command: str
    params: list[str]

    @property
    def nick(self) -> str:
        return self.prefix.split("!", 1)[0] if self.prefix else ""


def _unescape_tag(v: str) -> str:
    return (
        v.replace(r"\s", " ").replace(r"\:", ";").replace(r"\\", "\\")
        .replace(r"\r", "\r").replace(r"\n", "\n")
    )


def parse_irc(line: str) -> IrcLine:
    tags: dict[str, str] = {}
    prefix = ""
    rest = line.rstrip("\r\n")
    if rest.startswith("@"):
        raw, rest = rest[1:].split(" ", 1)
        for part in raw.split(";"):
            k, _, v = part.partition("=")
            tags[k] = _unescape_tag(v)
    if rest.startswith(":"):
        prefix, rest = rest[1:].split(" ", 1)
    trailing = None
    if " :" in rest:
        rest, trailing = rest.split(" :", 1)
    parts = rest.split()
    command, params = (parts[0] if parts else ""), parts[1:]
    if trailing is not None:
        params.append(trailing)
    return IrcLine(tags, prefix, command, params)


def to_chat_message(irc: IrcLine) -> ChatMessage | None:
    if irc.command != "PRIVMSG" or len(irc.params) < 2:
        return None
    badges = irc.tags.get("badges", "")
    return ChatMessage(
        user=irc.tags.get("display-name") or irc.nick,
        text=irc.params[1],
        platform="twitch",
        is_mod=irc.tags.get("mod") == "1" or "broadcaster" in badges,
        is_sub=irc.tags.get("subscriber") == "1",
        first_msg=irc.tags.get("first-msg") == "1",
    )


class TwitchChat:
    def __init__(self, channel: str, batcher: ChatBatcher, nick: str = "", token: str = "") -> None:
        self.channel = channel.lower().lstrip("#")
        self.batcher = batcher
        self.nick = nick.lower() if nick and token else f"justinfan{random.randint(10000, 99999)}"
        self.token = token if token.startswith("oauth:") or not token else f"oauth:{token}"

    async def run(self) -> None:
        backoff = 2.0
        while True:
            try:
                await self._session()
                backoff = 2.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("twitch chat disconnected", error=str(exc), retry_in=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _session(self) -> None:
        reader, writer = await asyncio.open_connection(HOST, PORT, ssl=ssl.create_default_context())
        try:
            send = lambda s: writer.write((s + "\r\n").encode())  # noqa: E731
            send("CAP REQ :twitch.tv/tags twitch.tv/commands")
            if self.token:
                send(f"PASS {self.token}")
            send(f"NICK {self.nick}")
            send(f"JOIN #{self.channel}")
            await writer.drain()
            logger.info("twitch chat connected", channel=self.channel, anonymous=not self.token)
            while line := await reader.readline():
                irc = parse_irc(line.decode("utf-8", "replace"))
                if irc.command == "PING":
                    send("PONG :" + (irc.params[-1] if irc.params else "tmi.twitch.tv"))
                    await writer.drain()
                elif irc.command == "RECONNECT":
                    return
                elif (msg := to_chat_message(irc)) is not None:
                    self.batcher.add(msg)
        finally:
            writer.close()
