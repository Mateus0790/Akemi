"""Ponte HTTP: qualquer app de chat entrega as mensagens para a secretária.

Uma porta pequena que recebe ``POST /mensagem`` e devolve a resposta dela. Serve
para ligar o WhatsApp (por um app de resposta automática no celular, ou um
script com whatsapp-web.js), Discord, SMS, n8n... Tudo em docs/SECRETARIA.md.

Pedido (JSON)::

    {"plataforma": "whatsapp", "contato": "Ana", "id": "5511999999999",
     "texto": "oi, o Mateus tá aí?", "grupo": false, "do_dono": false}

Também entende o formato do "servidor próprio" do AutoResponder
(``{"query": {"sender": ..., "message": ..., "isGroup": ...}}``).

Resposta (sempre 200 com o token certo)::

    {"resposta": "Oi! Aqui é a Akemi...", "replies": [{"message": "Oi! Aqui é a Akemi..."}]}

Resposta vazia (``"resposta": ""``, ``"replies": []``) = ela fica quieta.
A resposta vem marcada com 🤖, porque sai pela sua conta e quem recebe vê seu
nome; ``"marcar": false`` tira a marca (ex.: quando a ponte é um bot próprio).
``do_dono: true`` avisa que foi você que escreveu na conversa: ela sai de cena
com essa pessoa por um tempo.

Segurança: só com o token (``Authorization: Bearer``, ``X-Akemi-Token`` ou
``?token=``), comparado em tempo constante; corpo limitado; nada de servir
arquivo ou redirecionar. Tudo o que chega é ``Trust.PUBLIC``.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import structlog

from . import Mensagem, Secretaria, assinar

logger = structlog.get_logger()

BODY_LIMIT = 16 * 1024
MAX_HEADER_LINE = 8192
MAX_HEADERS = 50
READ_TIMEOUT_S = 5.0
REPLY_TIMEOUT_S = 45.0
MIN_TOKEN_LEN = 16
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost")

# Pacote do app (AutoResponder manda isso) -> plataforma.
PACKAGES = {
    "com.whatsapp": "whatsapp", "com.whatsapp.w4b": "whatsapp", "org.telegram.messenger": "telegram",
    "org.thunderdog.challegram": "telegram", "com.instagram.android": "instagram",
    "com.facebook.orca": "messenger", "com.discord": "discord", "com.google.android.apps.messaging": "sms",
    "org.thoughtcrime.securesms": "signal",
}
REASONS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
           413: "Payload Too Large", 500: "Internal Server Error"}


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "sim", "yes")
    return bool(value)


def parse_payload(data: Any) -> tuple[Mensagem, bool] | None:
    """O JSON da ponte (ou do AutoResponder) -> (mensagem, foi o dono que escreveu?)."""
    if not isinstance(data, dict):
        return None
    query = data.get("query")
    if isinstance(query, dict):  # AutoResponder
        sender = str(query.get("sender") or "").strip()
        group = _flag(query.get("isGroup"))
        if group and query.get("groupParticipant"):
            sender = f"{query['groupParticipant']} ({sender})"
        package = str(data.get("messengerPackageName") or data.get("appPackageName") or "")
        msg = Mensagem(sender[:80] or "alguém", str(query.get("message") or ""),
                       PACKAGES.get(package, "chat"), sender[:120], grupo=group)
        return msg, False
    text = data.get("texto", data.get("text"))
    who = data.get("contato", data.get("de", data.get("from")))
    if text is None or who is None:
        return None
    platform = str(data.get("plataforma") or data.get("platform") or "chat").strip().lower()[:30] or "chat"
    msg = Mensagem(str(who).strip()[:80] or "alguém", str(text), platform,
                   str(data.get("id") or data.get("contato_id") or "").strip()[:120],
                   grupo=_flag(data.get("grupo", data.get("group", False))))
    return msg, _flag(data.get("do_dono", data.get("fromMe", False)))


def reply_body(text: str) -> dict:
    return {"resposta": text, "replies": [{"message": text}] if text else []}


class PonteChats:
    def __init__(self, secretaria: Secretaria, token: str, host: str = "127.0.0.1", port: int = 8767,
                 reply_timeout_s: float = REPLY_TIMEOUT_S) -> None:
        if len(token) < MIN_TOKEN_LEN:
            raise ValueError(f"a ponte dos chats precisa de um token com pelo menos {MIN_TOKEN_LEN} caracteres")
        self.secretaria = secretaria
        self._token = token.encode()
        self.host = host
        self.port = port
        self.reply_timeout_s = reply_timeout_s
        self.received = 0
        self.rejected = 0
        self._server: asyncio.base_events.Server | None = None
        self._lock = asyncio.Lock()  # uma resposta por vez: a ordem das mensagens fica certa

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        logger.info("chat bridge listening", host=self.host, port=self.port)
        return self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ------------------------------------------------------------- HTTP
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, body = 400, {"erro": "pedido inválido"}
        try:
            request = await asyncio.wait_for(self._read(reader), READ_TIMEOUT_S)
            status, body = await self._answer(*request)
        except (asyncio.TimeoutError, asyncio.LimitOverrunError, asyncio.IncompleteReadError,
                ValueError, ConnectionError):
            status, body = 400, {"erro": "pedido inválido"}
        except _Reject as rej:
            status, body = rej.status, {"erro": rej.why}
        except Exception:  # nunca derruba a ponte
            logger.exception("chat bridge request failed")
            status, body = 500, {"erro": "deu ruim aqui"}
        if status != 200:
            self.rejected += 1
        payload = json.dumps(body, ensure_ascii=False).encode()
        head = (f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n")
        try:
            writer.write(head.encode() + payload)
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    async def _line(self, reader: asyncio.StreamReader) -> str:
        raw = await reader.readuntil(b"\n")
        if len(raw) > MAX_HEADER_LINE:
            raise ValueError("linha grande demais")
        return raw.decode("latin-1").rstrip("\r\n")

    async def _read(self, reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str], bytes]:
        method, _, rest = (await self._line(reader)).partition(" ")
        target = rest.partition(" ")[0]
        headers: dict[str, str] = {}
        for _ in range(MAX_HEADERS + 1):
            line = await self._line(reader)
            if not line:
                break
            name, sep, value = line.partition(":")
            if not sep:
                raise ValueError("cabeçalho inválido")
            headers[name.strip().lower()] = value.strip()
        else:
            raise ValueError("cabeçalhos demais")
        url = urlsplit(target)
        if url.path.rstrip("/") != "/mensagem":
            raise _Reject(404, "use POST /mensagem")
        if method != "POST":
            raise _Reject(405, "use POST /mensagem")
        if not self._authorized(headers, url.query):
            raise _Reject(403, "token inválido")
        try:
            length = int(headers.get("content-length", ""))
        except ValueError as exc:
            raise ValueError("sem Content-Length") from exc
        if length > BODY_LIMIT:
            raise _Reject(413, "mensagem grande demais")
        if length <= 0:
            raise ValueError("corpo vazio")
        return method, url.path, headers, await reader.readexactly(length)

    def _authorized(self, headers: dict[str, str], query: str) -> bool:
        auth = headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-akemi-token", "")
        if not token:
            token = (parse_qs(query).get("token") or [""])[0]
        return bool(token) and hmac.compare_digest(token.encode(), self._token)

    async def _answer(self, method: str, path: str, headers: dict[str, str], raw: bytes) -> tuple[int, dict]:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("JSON inválido") from exc
        parsed = parse_payload(data)
        if parsed is None:
            raise _Reject(400, 'faltou "contato" e "texto"')
        msg, from_owner = parsed
        self.received += 1
        if from_owner:
            self.secretaria.took_over(msg)
            return 200, reply_body("")
        async with self._lock:
            try:
                reply = await asyncio.wait_for(self.secretaria.receive(msg), self.reply_timeout_s)
            except asyncio.TimeoutError:
                logger.warning("chat bridge: reply took too long")
                reply = None
        mark = not isinstance(data, dict) or data.get("marcar", True) is not False
        return 200, reply_body(assinar(reply or "") if mark else reply or "")


class _Reject(Exception):
    def __init__(self, status: int, why: str) -> None:
        super().__init__(why)
        self.status = status
        self.why = why
