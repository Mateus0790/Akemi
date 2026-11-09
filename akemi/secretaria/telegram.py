"""Telegram: a secretária responde no Telegram, pela API oficial de bots.

Dois jeitos (docs/SECRETARIA.md):

- **Na sua própria conta** (Telegram Business, do Telegram Premium): em
  Configurações → Telegram Business → Chatbots, conecte o bot. As mensagens que
  as pessoas mandam para você chegam como ``business_message`` e ela responde no
  seu chat. Quando você mesmo responde, ela sai de cena com aquela pessoa.
- **No bot**: quem escrever direto para o bot fala com ela, como um recado.

Só ``httpx`` (já é dependência), por long polling: nada de porta aberta.
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from . import Mensagem, Secretaria

logger = structlog.get_logger()

API = "https://api.telegram.org"
POLL_TIMEOUT_S = 30
ALLOWED_UPDATES = ["message", "business_message"]
# O que não é texto vira uma descrição, para virar recado do mesmo jeito.
MEDIA = (("voice", "mandou um áudio"), ("audio", "mandou um áudio"), ("video_note", "mandou um vídeo"),
         ("video", "mandou um vídeo"), ("photo", "mandou uma foto"), ("sticker", "mandou uma figurinha"),
         ("document", "mandou um arquivo"), ("location", "mandou uma localização"),
         ("contact", "mandou um contato"))


class TelegramError(RuntimeError):
    pass


class TelegramAuthError(TelegramError):
    """Token recusado: não adianta tentar de novo."""


def message_text(msg: dict) -> str:
    text = str(msg.get("text") or msg.get("caption") or "").strip()
    for key, what in MEDIA:
        if key in msg:
            return f"({what}) {text}".strip()
    return text


def sender_name(user: dict) -> str:
    name = " ".join(str(user.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    return name or str(user.get("username") or "alguém")


class TelegramChats:
    def __init__(self, secretaria: Secretaria, token: str, *, client: httpx.AsyncClient | None = None,
                 base_url: str = API, poll_timeout_s: int = POLL_TIMEOUT_S) -> None:
        if not token.strip():
            raise ValueError("faltou o TELEGRAM_BOT_TOKEN")
        self.secretaria = secretaria
        self._url = f"{base_url.rstrip('/')}/bot{token.strip()}"
        self._client = client
        self.poll_timeout_s = poll_timeout_s
        self.offset: int | None = None
        self.answered = 0

    async def _call(self, method: str, payload: dict) -> object:
        client = self._client
        if client is None:
            client = self._client = httpx.AsyncClient(timeout=self.poll_timeout_s + 15)
        try:
            resp = await client.post(f"{self._url}/{method}", json=payload)
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            # A mensagem do erro pode ter a URL, e a URL tem o token: só o tipo.
            raise TelegramError(f"{method} falhou ({type(exc).__name__})") from None
        if not isinstance(data, dict) or not data.get("ok"):
            desc = data.get("description", "") if isinstance(data, dict) else ""
            if resp.status_code == 401 or (isinstance(data, dict) and data.get("error_code") == 401):
                raise TelegramAuthError(f"{method}: token recusado ({desc or 401})")
            raise TelegramError(f"{method}: {desc or resp.status_code}")
        return data.get("result")

    async def poll_once(self) -> int:
        """Busca e trata as mensagens novas. Devolve quantas chegaram."""
        payload: dict = {"timeout": self.poll_timeout_s, "allowed_updates": ALLOWED_UPDATES}
        if self.offset is not None:
            payload["offset"] = self.offset
        updates = await self._call("getUpdates", payload)
        if not isinstance(updates, list):
            return 0
        for update in updates:
            if isinstance(update, dict) and isinstance(update.get("update_id"), int):
                self.offset = update["update_id"] + 1
                try:
                    await self.handle(update)
                except TelegramError as exc:
                    logger.warning("telegram reply failed", error=str(exc))
        return len(updates)

    async def handle(self, update: dict) -> None:
        business = None
        msg = update.get("business_message")
        if isinstance(msg, dict):
            business = msg.get("business_connection_id")
        else:
            msg = update.get("message")
        if not isinstance(msg, dict):
            return
        chat = msg.get("chat") or {}
        user = msg.get("from") or {}
        if user.get("is_bot") or msg.get("sender_business_bot") or chat.get("id") is None:
            return
        private = chat.get("type") == "private"
        name = sender_name(user)
        if not private:
            name = f"{name} ({chat.get('title') or 'grupo'})"
        contact = Mensagem(name[:80], message_text(msg), "telegram", str(chat["id"]), grupo=not private)
        if business and private and user.get("id") != chat.get("id"):
            # Num chat da sua conta, quem não é o outro é você: você assumiu a conversa.
            self.secretaria.took_over(contact)
            return
        reply = await self.secretaria.receive(contact)
        if not reply:
            return
        payload = {"chat_id": chat["id"], "text": reply}
        if business:
            payload["business_connection_id"] = business
        await self._call("sendMessage", payload)
        self.answered += 1

    async def run(self) -> None:
        """Loop de long polling (supervisionado: se cair, reinicia sozinho)."""
        try:
            me = await self._call("getMe", {})
        except TelegramAuthError as exc:
            # Token errado: tentar de novo não resolve. Fica desligado até você corrigir.
            logger.error("telegram off: check TELEGRAM_BOT_TOKEN", error=str(exc))
            if "telegram" in self.secretaria.canais:
                self.secretaria.canais.remove("telegram")
            return
        logger.info("telegram connected", bot=(me or {}).get("username") if isinstance(me, dict) else None)
        while True:
            await self.poll_once()
            await asyncio.sleep(0)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
