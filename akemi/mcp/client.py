"""Cliente MCP (Model Context Protocol) mínimo, só com asyncio e httpx.

Fala com servidores MCP de outros aplicativos para a Akemi usar as
ferramentas deles. Dois jeitos de conectar, os mesmos do Claude Desktop,
Cursor e VS Code:

- **stdio**: a Akemi abre o programa do servidor (``npx ...``, ``uvx ...``,
  um ``.exe``) e conversa pela entrada/saída dele, uma mensagem JSON por linha.
- **HTTP** (Streamable HTTP): o servidor já está rodando, local ou na nuvem,
  e cada mensagem vai num POST. A resposta vem em JSON ou em SSE.

Só o necessário para usar ferramentas: ``initialize``, ``tools/list`` e
``tools/call``. Pedidos do servidor (``ping``) são respondidos; o resto
(sampling, elicitation, roots) é recusado educadamente.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import itertools
import json
import os
import shutil
import sys
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "akemi", "version": "0.2.0"}
MAX_LINE = 16 * 1024 * 1024  # listas de ferramentas podem ser grandes


class MCPError(RuntimeError):
    """O servidor MCP não respondeu, recusou ou a ferramenta deu erro."""

    expected = True  # o ActionRegistry registra sem stack trace


@dataclass
class Tool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> Tool:
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description") or data.get("title") or ""),
            input_schema=data.get("inputSchema") if isinstance(data.get("inputSchema"), dict) else {},
            annotations=data.get("annotations") if isinstance(data.get("annotations"), dict) else {},
        )

    @property
    def read_only(self) -> bool:
        """O servidor diz que a ferramenta só lê (e não destrói nada)."""
        a = self.annotations
        return a.get("readOnlyHint") is True and a.get("destructiveHint") is not True


def result_to_text(result: dict[str, Any], max_chars: int = 4000) -> str:
    """O resultado de ``tools/call`` em texto, para o cérebro dela ler."""
    parts: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind == "image":
            parts.append("[imagem]")
        elif kind == "audio":
            parts.append("[áudio]")
        elif kind == "resource":
            res = item.get("resource") or {}
            parts.append(str(res.get("text") or f"[arquivo {res.get('uri', '')}]"))
        elif kind == "resource_link":
            parts.append(f"[link: {item.get('name') or ''} {item.get('uri', '')}]".replace("  ", " "))
    if not any(p.strip() for p in parts) and result.get("structuredContent") is not None:
        parts = [json.dumps(result["structuredContent"], ensure_ascii=False)]
    text = "\n".join(p for p in parts if p).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + " [...cortado]"
    return text


# ================================================================ conexões
class Connection(abc.ABC):
    """Uma ligação JSON-RPC com o servidor. Subclasses: stdio e HTTP."""

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.on_notification: Callable[[str, dict[str, Any]], None] | None = None

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 60.0) -> Any: ...

    @abc.abstractmethod
    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None: ...

    @abc.abstractmethod
    async def close(self) -> None: ...

    @property
    @abc.abstractmethod
    def alive(self) -> bool: ...

    def _answer_server_request(self, msg: dict[str, Any]) -> dict[str, Any]:
        """O servidor pediu algo para nós (ex.: ``ping``)."""
        if msg.get("method") == "ping":
            return {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
        return {
            "jsonrpc": "2.0", "id": msg["id"],
            "error": {"code": -32601, "message": f"a Akemi não suporta {msg.get('method')}"},
        }

    def _notified(self, msg: dict[str, Any]) -> None:
        if self.on_notification is not None:
            try:
                self.on_notification(str(msg.get("method", "")), msg.get("params") or {})
            except Exception:
                logger.exception("mcp notification handler failed")


def _raise_rpc_error(msg: dict[str, Any]) -> None:
    err = msg.get("error")
    if err:
        if isinstance(err, dict):
            raise MCPError(str(err.get("message") or err))
        raise MCPError(str(err))


class StdioConnection(Connection):
    """Abre o programa do servidor e conversa pelo stdin/stdout dele."""

    def __init__(self, command: str, args: list[str] | None = None, *, env: dict[str, str] | None = None,
                 cwd: str | None = None) -> None:
        super().__init__()
        self.command = command
        self.args = list(args or [])
        self.env = env or {}
        self.cwd = cwd or None
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self.stderr_tail: deque[str] = deque(maxlen=20)
        self._write_lock = asyncio.Lock()

    def _resolve(self) -> str:
        # No Windows, "npx" é "npx.cmd": o which acha pela PATHEXT.
        found = shutil.which(self.command, path=self.env.get("PATH") or os.environ.get("PATH"))
        if found is None:
            raise MCPError(f"não achei o programa {self.command!r} (está instalado e no PATH?)")
        return found

    async def start(self) -> None:
        env = {**os.environ, **self.env}
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":  # sem janela de console piscando para cada servidor
            import subprocess

            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._resolve(), *self.args,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=env, cwd=self.cwd, limit=MAX_LINE, **kwargs,
            )
        except OSError as exc:
            raise MCPError(f"não consegui abrir {self.command!r}: {exc}") from exc
        self._reader = asyncio.create_task(self._read_loop(), name=f"mcp-stdio-{self.command}")
        self._stderr_task = asyncio.create_task(self._read_stderr(), name=f"mcp-stderr-{self.command}")

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None and self._reader is not None \
            and not self._reader.done()

    async def _send(self, msg: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None or self._proc.returncode is not None:
            raise MCPError("o servidor MCP fechou")
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                self._proc.stdin.write(data)
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise MCPError("o servidor MCP fechou") from exc

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    logger.debug("mcp non-json line", line=line[:200])
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:
                    await self._send(self._answer_server_request(msg))
                elif "method" in msg:
                    self._notified(msg)
                elif (fut := self._pending.pop(msg.get("id"), None)) is not None and not fut.done():
                    fut.set_result(msg)
        except (asyncio.CancelledError, MCPError):
            pass
        except Exception:
            logger.exception("mcp stdio reader failed")
        finally:
            why = " | ".join(list(self.stderr_tail)[-3:])
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(MCPError("o servidor MCP fechou" + (f": {why}" if why else "")))
            self._pending.clear()

    async def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        with contextlib.suppress(Exception):
            while line := await self._proc.stderr.readline():
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    self.stderr_tail.append(text[:300])

    async def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 60.0) -> Any:
        if not self.alive:
            raise MCPError("o servidor MCP não está rodando")
        rid = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            await self._send(msg)
            reply = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            raise MCPError(f"o servidor MCP demorou mais de {int(timeout)} s para responder") from exc
        finally:
            self._pending.pop(rid, None)
        _raise_rpc_error(reply)
        return reply.get("result")

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self._send(msg)

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                if proc.stdin is not None:
                    proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 2.0)
            except (asyncio.TimeoutError, Exception):
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 2.0)
                except (asyncio.TimeoutError, Exception):
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
        for task in (self._reader, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


def iter_sse(lines) -> Any:
    """Junta as linhas de um stream SSE em mensagens (o ``data:`` de cada evento)."""
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield "\n".join(data)


class HttpConnection(Connection):
    """Streamable HTTP: um POST por mensagem; resposta em JSON ou SSE."""

    def __init__(self, url: str, *, headers: dict[str, str] | None = None, client: httpx.AsyncClient | None = None,
                 timeout_s: float = 60.0) -> None:
        super().__init__()
        self.url = url
        self.headers = headers or {}
        self._client = client
        self._own_client = client is None
        self.timeout_s = timeout_s
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self._open = False

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s, connect=10.0))
        self._open = True

    @property
    def alive(self) -> bool:
        return self._open

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            h["MCP-Protocol-Version"] = self.protocol_version
        return h

    async def _post(self, msg: dict[str, Any], *, timeout: float) -> dict[str, Any] | None:
        assert self._client is not None
        rid = msg.get("id")
        try:
            async with self._client.stream("POST", self.url, json=msg, headers=self._headers(),
                                           timeout=timeout) as resp:
                if sid := resp.headers.get("mcp-session-id"):
                    self.session_id = sid
                if resp.status_code == 404 and self.session_id:
                    self._open = False  # sessão expirou: reconecta na próxima
                    raise MCPError("a sessão com o servidor MCP expirou")
                if resp.status_code in (401, 403):
                    raise MCPError(f"o servidor MCP recusou o acesso (HTTP {resp.status_code}): confira o token")
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:200]
                    raise MCPError(f"o servidor MCP respondeu HTTP {resp.status_code}: {body}")
                if rid is None:
                    return None  # notificação: 202 sem corpo
                ctype = resp.headers.get("content-type", "")
                if "text/event-stream" in ctype:
                    return await self._from_sse(resp, rid)
                body = await resp.aread()
                if not body.strip():
                    raise MCPError("o servidor MCP respondeu vazio")
                data = json.loads(body)
                for item in data if isinstance(data, list) else [data]:
                    if isinstance(item, dict) and item.get("id") == rid and "method" not in item:
                        return item
                raise MCPError("o servidor MCP não respondeu o pedido")
        except httpx.TimeoutException as exc:
            raise MCPError(f"o servidor MCP demorou mais de {int(timeout)} s para responder") from exc
        except httpx.HTTPError as exc:
            raise MCPError(f"sem conexão com o servidor MCP: {type(exc).__name__}: {exc}") from exc
        except ValueError as exc:
            raise MCPError("o servidor MCP mandou uma resposta inválida") from exc

    async def _from_sse(self, resp: httpx.Response, rid: Any) -> dict[str, Any]:
        lines: list[str] = []
        stream = resp.aiter_lines()
        try:
            async for line in stream:
                lines.append(line)
                if line != "":
                    continue
                for raw in iter_sse(lines):
                    found = await self._handle_sse_message(raw, rid)
                    if found is not None:
                        return found
                lines = []
        finally:
            await stream.aclose()
        for raw in iter_sse(lines):
            found = await self._handle_sse_message(raw, rid)
            if found is not None:
                return found
        raise MCPError("o servidor MCP fechou o stream sem responder")

    async def _handle_sse_message(self, raw: str, rid: Any) -> dict[str, Any] | None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(msg, dict):
            return None
        if "method" in msg and "id" in msg:
            with contextlib.suppress(MCPError):
                await self._post(self._answer_server_request(msg), timeout=10.0)
            return None
        if "method" in msg:
            self._notified(msg)
            return None
        return msg if msg.get("id") == rid else None

    async def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 60.0) -> Any:
        if not self._open:
            raise MCPError("a conexão com o servidor MCP está fechada")
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
        if params is not None:
            msg["params"] = params
        reply = await self._post(msg, timeout=timeout)
        assert reply is not None
        _raise_rpc_error(reply)
        return reply.get("result")

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self._post(msg, timeout=self.timeout_s)

    async def close(self) -> None:
        if self._client is not None and self.session_id and self._open:
            with contextlib.suppress(Exception):
                await self._client.delete(self.url, headers=self._headers(), timeout=5.0)
        self._open = False
        self.session_id = None
        if self._own_client and self._client is not None:
            await self._client.aclose()
            self._client = None


# ================================================================ sessão
class MCPSession:
    """Um servidor MCP conectado: inicializa, lista e chama ferramentas."""

    def __init__(self, connection: Connection, *, timeout_s: float = 60.0) -> None:
        self.connection = connection
        self.timeout_s = timeout_s
        self.server_info: dict[str, Any] = {}
        self.instructions = ""
        self.tools_changed = False
        connection.on_notification = self._on_notification

    def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "notifications/tools/list_changed":
            self.tools_changed = True

    async def initialize(self, *, timeout: float | None = None) -> None:
        await self.connection.start()
        result = await self.connection.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }, timeout=timeout or self.timeout_s) or {}
        self.server_info = result.get("serverInfo") or {}
        self.instructions = str(result.get("instructions") or "")
        if isinstance(self.connection, HttpConnection):
            self.connection.protocol_version = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        if "tools" not in (result.get("capabilities") or {"tools": {}}):
            logger.info("mcp server has no tools", server=self.server_info.get("name"))
        await self.connection.notify("notifications/initialized")

    async def list_tools(self) -> list[Tool]:
        tools: list[Tool] = []
        cursor: str | None = None
        for _ in range(50):  # paginação, com limite para servidor bugado
            result = await self.connection.request(
                "tools/list", {"cursor": cursor} if cursor else {}, timeout=self.timeout_s,
            ) or {}
            tools += [Tool.from_wire(t) for t in result.get("tools") or [] if isinstance(t, dict) and t.get("name")]
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self.tools_changed = False
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any], *, timeout: float | None = None) -> dict:
        result = await self.connection.request(
            "tools/call", {"name": name, "arguments": arguments}, timeout=timeout or self.timeout_s,
        )
        return result if isinstance(result, dict) else {}

    async def close(self) -> None:
        await self.connection.close()

    @property
    def alive(self) -> bool:
        return self.connection.alive
