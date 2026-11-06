"""Recebe os eventos dos hooks (Modo Casca): só de 127.0.0.1 e só com o token.

Ao ligar, abre uma porta livre em 127.0.0.1, gera um token novo e grava os dois
no arquivo de rota (``~/.akemi/casca.json``), que o hook lê. Ao desligar, apaga
o arquivo (se ainda for o dela).

Um HTTP mínimo, de propósito: só ``POST /event``, com tamanho limitado, token
comparado em tempo constante e nada de redirecionar, servir arquivo ou ecoar.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from .hook import BODY_LIMIT, TOKEN_HEADER, route_path

logger = structlog.get_logger()

MAX_HEADER_LINE = 8192
MAX_HEADERS = 50
READ_TIMEOUT_S = 3.0


class CascaListener:
    def __init__(
        self,
        on_event: Callable[[dict[str, Any]], None],
        route_file: Path | None = None,
        host: str = "127.0.0.1",
    ) -> None:
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError("o Modo Casca só escuta no próprio PC")
        self.on_event = on_event
        self.route_file = route_file or route_path()
        self.host = host
        self.token = secrets.token_urlsafe(32)
        self.port = 0
        self.received = 0
        self.rejected = 0
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, self.host, 0)
        self.port = self._server.sockets[0].getsockname()[1]
        self._write_route()
        logger.info("casca listening", port=self.port, route=str(self.route_file))
        return self.port

    def _write_route(self) -> None:
        self.route_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.route_file.with_name(self.route_file.name + f".{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"port": self.port, "token": self.token}, fh)
        os.replace(tmp, self.route_file)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        try:
            route = json.loads(self.route_file.read_text(encoding="utf-8"))
            if isinstance(route, dict) and route.get("token") == self.token:
                self.route_file.unlink()
        except (OSError, ValueError):
            pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status = 400
        try:
            status = await asyncio.wait_for(self._read(reader), READ_TIMEOUT_S)
        except (asyncio.TimeoutError, asyncio.LimitOverrunError, asyncio.IncompleteReadError,
                ValueError, ConnectionError):
            status = 400
        except Exception:  # nunca derruba o servidor
            logger.exception("casca request failed")
            status = 500
        if status != 204:
            self.rejected += 1
        reason = {204: "No Content", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
                  413: "Payload Too Large", 500: "Internal Server Error"}.get(status, "Error")
        try:
            writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
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

    async def _read(self, reader: asyncio.StreamReader) -> int:
        method, _, rest = (await self._line(reader)).partition(" ")
        path = rest.partition(" ")[0]
        headers: dict[str, str] = {}
        for _ in range(MAX_HEADERS + 1):
            line = await self._line(reader)
            if not line:
                break
            name, sep, value = line.partition(":")
            if not sep:
                return 400
            headers[name.strip().lower()] = value.strip()
        else:
            return 400
        if method != "POST" or path != "/event":
            return 404
        token = headers.get(TOKEN_HEADER.lower(), "")
        if not hmac.compare_digest(token.encode(), self.token.encode()):
            return 403
        try:
            length = int(headers.get("content-length", ""))
        except ValueError:
            return 400
        if not 0 < length <= BODY_LIMIT:
            return 413 if length > BODY_LIMIT else 400
        body = await reader.readexactly(length)
        try:
            event = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return 400
        if not isinstance(event, dict):
            return 400
        self.received += 1
        self.on_event(event)
        return 204
