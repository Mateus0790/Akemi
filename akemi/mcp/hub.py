"""Ferramentas de outros aplicativos (servidores MCP) viram ações da Akemi.

Lê o mesmo ``mcp.json`` do Claude Desktop, Cursor e VS Code::

    {
      "mcpServers": {
        "arquivos": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/voce/Documents"]},
        "github": {"url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"}}
      }
    }

Cada ferramenta vira a ação ``<servidor>.<ferramenta>`` (ex.: ``arquivos.read_file``).
As regras de sempre continuam valendo, porque passam pelo ActionRegistry:

- Só o dono pede (o chat da live e visitas nunca executam nada).
- Ferramenta que o servidor marca como só leitura roda direto; o resto pede
  "sim" antes (``"confirmar": "sempre"`` ou ``"nunca"`` muda isso por servidor).
- Nada de app entra na iniciativa: ela nunca usa um app por conta própria.
- O resultado volta para o cérebro como *dados, não instruções*, e as tags de
  ação que vierem na resposta sobre ele são ignoradas.

Chaves só da Akemi em cada servidor: ``confirmar`` (auto|sempre|nunca),
``ferramentas`` (só estas), ``bloquear`` (nunca estas), ``timeout`` (segundos)
e ``desligado`` (ou ``disabled``). ``${VAR}`` puxa do ambiente ou do ``.env``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from akemi.actions.registry import Action, ActionRegistry, Tier

from .client import Connection, HttpConnection, MCPError, MCPSession, StdioConnection, Tool, result_to_text

logger = structlog.get_logger()

VAR_RE = re.compile(r"\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}")
CONFIRM_MODES = ("auto", "sempre", "nunca")
_CONFIRM_ALIASES = {"always": "sempre", "never": "nunca", "auto": "auto"}


@dataclass
class ServerConfig:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    confirm: str = "auto"
    only: list[str] | None = None  # "ferramentas": só estas
    block: list[str] = field(default_factory=list)  # "bloquear": nunca estas
    timeout_s: float | None = None
    source: str = ""  # de qual arquivo veio

    @property
    def transport(self) -> str:
        return "http" if self.url else "stdio"


def action_prefix(name: str) -> str:
    return re.sub(r"[^\w-]+", "_", name.strip().lower()).strip("_") or "app"


def action_name(server: str, tool: str) -> str:
    tool_part = re.sub(r"[^\w.-]+", "_", tool.strip().lower()).strip("_")
    return f"{action_prefix(server)}.{tool_part}"


def env_for_config(dotenv_path: Path | str = ".env") -> dict[str, str]:
    """Variáveis para ``${VAR}``: o ``.env`` e, por cima, o ambiente."""
    values: dict[str, str] = {}
    try:
        from dotenv import dotenv_values

        values = {k: v for k, v in dotenv_values(dotenv_path).items() if v is not None}
    except Exception:  # pragma: no cover - sem .env ou sem python-dotenv
        pass
    return {**values, **os.environ}


def _expand(value: Any, env: Mapping[str, str], missing: set[str]) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            if m.group(1) not in env:
                missing.add(m.group(1))
            return env.get(m.group(1), "")

        return VAR_RE.sub(sub, value)
    if isinstance(value, list):
        return [_expand(v, env, missing) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v, env, missing) for k, v in value.items()}
    return value


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value or [] if str(v).strip()]


def parse_config(data: Any, *, env: Mapping[str, str] | None = None, source: str = "") -> tuple[list[ServerConfig], list[str]]:
    """Lê o conteúdo de um ``mcp.json``. Devolve (servidores, problemas)."""
    env = env if env is not None else env_for_config()
    problems: list[str] = []
    if not isinstance(data, dict):
        return [], [f"{source}: o arquivo precisa ser um objeto JSON"]
    servers = data.get("mcpServers", data.get("servers"))
    if servers is None and isinstance(data.get("mcp"), dict):  # VS Code settings.json
        servers = data["mcp"].get("servers")
    if not isinstance(servers, dict):
        return [], [f"{source}: faltou a chave \"mcpServers\""]
    out: list[ServerConfig] = []
    for name, raw in servers.items():
        if not isinstance(raw, dict):
            problems.append(f"{name}: configuração inválida")
            continue
        if raw.get("desligado") or raw.get("disabled"):
            continue
        missing: set[str] = set()
        entry = _expand(raw, env, missing)
        if missing:
            problems.append(f"{name}: variáveis sem valor: {', '.join(sorted(missing))}")
        kind = str(entry.get("type") or entry.get("transport") or "").lower()
        if kind == "sse":
            problems.append(f"{name}: o transporte SSE antigo não é suportado; use a URL Streamable HTTP (geralmente termina em /mcp)")
            continue
        url = str(entry.get("url") or entry.get("serverUrl") or "").strip()
        command = str(entry.get("command") or "").strip()
        if not url and not command:
            problems.append(f"{name}: precisa de \"command\" (programa) ou \"url\" (servidor HTTP)")
            continue
        confirm = _CONFIRM_ALIASES.get(str(entry.get("confirmar") or entry.get("confirm") or "auto").lower(), "")
        if confirm not in CONFIRM_MODES:
            problems.append(f"{name}: \"confirmar\" deve ser auto, sempre ou nunca (usei sempre)")
            confirm = "sempre"
        only = entry.get("ferramentas", entry.get("tools"))
        timeout = entry.get("timeout")
        try:
            timeout_s = float(timeout) if timeout not in (None, "") else None
        except (TypeError, ValueError):
            timeout_s = None
            problems.append(f"{name}: \"timeout\" precisa ser um número de segundos")
        out.append(ServerConfig(
            name=str(name),
            command=command,
            args=_str_list(entry.get("args")),
            env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
            cwd=str(entry.get("cwd") or ""),
            url=url,
            headers={str(k): str(v) for k, v in (entry.get("headers") or {}).items()},
            confirm=confirm,
            only=_str_list(only) if only is not None else None,
            block=_str_list(entry.get("bloquear", entry.get("block"))),
            timeout_s=timeout_s,
            source=source,
        ))
    return out, problems


def load_config(paths: Iterable[Path | str], *, env: Mapping[str, str] | None = None) -> tuple[list[ServerConfig], list[str]]:
    """Junta os servidores de vários arquivos. Nome repetido: vale o primeiro."""
    env = env if env is not None else env_for_config()
    servers: dict[str, ServerConfig] = {}
    problems: list[str] = []
    for p in paths:
        path = Path(os.path.expandvars(str(p))).expanduser()
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            problems.append(f"{path}: não consegui ler ({exc})")
            continue
        found, issues = parse_config(data, env=env, source=str(path))
        problems += issues
        for cfg in found:
            servers.setdefault(action_prefix(cfg.name), cfg)
    return list(servers.values()), problems


def parse_paths(value: str) -> list[str]:
    """``MCP_CONFIG``: um caminho, vários separados por ``;`` ou uma lista JSON."""
    value = (value or "").strip()
    if value.startswith("["):
        try:
            return [str(v) for v in json.loads(value) if str(v).strip()]
        except ValueError:
            pass
    return [p.strip() for p in value.split(";") if p.strip()]


# ============================================================== ações
def _short(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def describe_params(schema: dict[str, Any], limit: int = 8) -> dict[str, str]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return {}
    required = set(schema.get("required") or [])
    out: dict[str, str] = {}
    for name, spec in list(props.items())[:limit]:
        spec = spec if isinstance(spec, dict) else {}
        bits = [_short(spec.get("description") or spec.get("title") or name, 80)]
        kind = spec.get("type")
        if isinstance(kind, list):
            kind = "/".join(str(k) for k in kind)
        if spec.get("enum"):
            bits.append("um de: " + ", ".join(str(v) for v in spec["enum"][:6]))
        elif kind:
            bits.append(str(kind))
        if name in required:
            bits.append("obrigatório")
        out[str(name)] = f"{bits[0]} ({', '.join(bits[1:])})" if len(bits) > 1 else bits[0]
    return out


def tier_for(tool: Tool, confirm: str) -> Tier:
    if confirm == "nunca":
        return Tier.FREE
    if confirm == "sempre":
        return Tier.CONFIRM
    return Tier.FREE if tool.read_only else Tier.CONFIRM


@dataclass
class ServerState:
    config: ServerConfig
    session: MCPSession | None = None
    tools: list[Tool] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    skipped: int = 0  # ferramentas que não couberam no limite
    error: str = ""
    retry_at: float = 0.0
    backoff_s: float = 30.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def connected(self) -> bool:
        return self.session is not None and self.session.alive


HealthFn = Callable[[str, bool, str, str], None]


class MCPHub:
    """Liga os servidores MCP, registra as ferramentas como ações e as chama."""

    def __init__(
        self,
        registry: ActionRegistry,
        servers: Iterable[ServerConfig],
        *,
        timeout_s: float = 60.0,
        connect_timeout_s: float = 90.0,
        max_tools: int = 60,
        result_max_chars: int = 4000,
        on_health: HealthFn | None = None,
        connection_factory: Callable[[ServerConfig], Connection] | None = None,
        retry_max_s: float = 600.0,
    ) -> None:
        self.registry = registry
        self.servers = {action_prefix(s.name): ServerState(s) for s in servers}
        self.timeout_s = timeout_s
        self.connect_timeout_s = connect_timeout_s
        self.max_tools = max_tools
        self.result_max_chars = result_max_chars
        self.on_health = on_health
        self._factory = connection_factory or self._connection_for
        self.retry_max_s = retry_max_s
        self.ready = asyncio.Event()

    # ---------------------------------------------------------- conexão
    def _connection_for(self, cfg: ServerConfig) -> Connection:
        timeout = cfg.timeout_s or self.timeout_s
        if cfg.url:
            return HttpConnection(cfg.url, headers=cfg.headers, timeout_s=timeout)
        return StdioConnection(cfg.command, cfg.args, env=cfg.env, cwd=cfg.cwd)

    def _health(self, st: ServerState, ok: bool, detail: str) -> None:
        if self.on_health is not None:
            self.on_health(f"mcp.{action_prefix(st.config.name)}", ok, detail, f"conexão com o {st.config.name}")

    async def _connect(self, st: ServerState) -> None:
        cfg = st.config
        if st.session is not None:
            await st.session.close()
            st.session = None
        session = MCPSession(self._factory(cfg), timeout_s=cfg.timeout_s or self.timeout_s)
        try:
            await asyncio.wait_for(session.initialize(timeout=self.connect_timeout_s), self.connect_timeout_s)
            tools = await session.list_tools()
        except BaseException as exc:
            await session.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, asyncio.TimeoutError):
                exc = MCPError(f"não respondeu em {int(self.connect_timeout_s)} s")
            tail = getattr(session.connection, "stderr_tail", None)
            why = str(exc) + (f" | {tail[-1]}" if tail and "fechou" not in str(exc) else "")
            st.error = why[:300]
            st.retry_at = time.monotonic() + st.backoff_s
            st.backoff_s = min(st.backoff_s * 2, self.retry_max_s)
            logger.warning("mcp server failed", server=cfg.name, error=st.error)
            self._health(st, False, st.error)
            return
        st.session, st.error, st.backoff_s = session, "", 30.0
        self._register(st, tools)
        logger.info("mcp server connected", server=cfg.name, tools=len(st.actions),
                    info=session.server_info.get("name", ""))
        self._health(st, True, f"{len(st.actions)} ferramenta(s)")

    def _register(self, st: ServerState, tools: list[Tool]) -> None:
        cfg = st.config
        for name in st.actions:  # lista nova substitui a antiga
            self.registry.remove(name)
        st.tools, st.actions, st.skipped = [], [], 0
        used = sum(len(s.actions) for s in self.servers.values())
        only = {t.lower() for t in cfg.only} if cfg.only is not None else None
        block = {t.lower() for t in cfg.block}
        for tool in tools:
            low = tool.name.lower()
            if (only is not None and low not in only) or low in block:
                continue
            name = action_name(cfg.name, tool.name)
            if (existing := self.registry.get(name)) is not None and name not in st.actions:
                logger.warning("mcp tool name taken", action=name, by=existing.name)
                continue
            if used >= self.max_tools:
                st.skipped += 1
                continue
            self.registry.add(self._action(st, tool, name))
            self.registry.never_initiative.add(name)
            st.tools.append(tool)
            st.actions.append(name)
            used += 1
        if st.skipped:
            logger.warning("mcp tools over the limit", server=cfg.name, skipped=st.skipped, limit=self.max_tools)

    def _action(self, st: ServerState, tool: Tool, name: str) -> Action:
        server, tool_name = st.config.name, tool.name
        desc = _short(tool.description or tool_name.replace("_", " "), 160)

        async def run(**args: Any) -> str:
            return await self.call(server, tool_name, args)

        def confirm(**args: Any) -> str:
            shown = ", ".join(f"{k}: {_short(v, 40)}" for k, v in args.items() if v not in (None, ""))
            return f"Posso usar o {server} para {tool_name.replace('_', ' ')}{f' ({shown})' if shown else ''}?"

        return Action(
            name, f"[app {server}] {desc}", run, tier_for(tool, st.config.confirm),
            describe_params(tool.input_schema), confirm=confirm, summarize=True,
        )

    async def start(self) -> None:
        """Conecta todos ao mesmo tempo. Quem falhar tenta de novo em :meth:`run`."""
        await asyncio.gather(*(self._connect(st) for st in self.servers.values()))
        self.ready.set()

    async def run(self) -> None:
        """Conecta e fica religando quem caiu (30 s, 60 s... até 10 min)."""
        if not self.ready.is_set():
            await self.start()
        while True:
            await asyncio.sleep(15)
            now = time.monotonic()
            for st in self.servers.values():
                if not st.connected and now >= st.retry_at:
                    async with st.lock:
                        if not st.connected:
                            await self._connect(st)

    # ---------------------------------------------------------- uso
    async def call(self, server: str, tool: str, args: dict[str, Any]) -> str:
        st = self.servers.get(action_prefix(server))
        if st is None:
            raise MCPError(f"não conheço o app {server}")
        async with st.lock:
            if not st.connected:
                await self._connect(st)
                if not st.connected:
                    raise MCPError(f"o {server} não está conectado: {st.error}")
            session = st.session
        assert session is not None
        try:
            result = await session.call_tool(tool, args, timeout=st.config.timeout_s or self.timeout_s)
        except MCPError:
            if not session.alive:  # caiu no meio: na próxima ela religa
                self._health(st, False, "caiu")
            raise
        text = result_to_text(result, self.result_max_chars)
        if session.tools_changed:
            try:
                self._register(st, await session.list_tools())
            except MCPError as exc:
                logger.warning("mcp tool refresh failed", server=server, error=str(exc))
        if result.get("isError"):
            raise MCPError(text or f"a ferramenta {tool} deu erro")
        return text or "feito"

    async def close(self) -> None:
        for st in self.servers.values():
            if st.session is not None:
                try:
                    await st.session.close()
                except Exception:  # pragma: no cover
                    logger.exception("mcp close failed", server=st.config.name)
                st.session = None

    # ---------------------------------------------------------- painel
    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "nome": st.config.name,
                "conectado": st.connected,
                "transporte": st.config.transport,
                "ferramentas": list(st.actions),
                "fora_do_limite": st.skipped,
                "erro": st.error,
            }
            for st in self.servers.values()
        ]

    def describe(self) -> str:
        if not self.servers:
            return "  nenhum app MCP configurado (veja docs/MCP.md)"
        lines = []
        for st in self.servers.values():
            cfg = st.config
            if st.connected:
                free = sum(1 for n in st.actions if (a := self.registry.get(n)) and a.tier == Tier.FREE)
                head = f"  {cfg.name}: conectado ({cfg.transport}), {len(st.actions)} ferramenta(s)"
                head += f", {free} sem pedir confirmação" if free else ", todas pedem confirmação"
                lines.append(head)
                if st.actions:
                    lines.append("    " + ", ".join(st.actions))
                if st.skipped:
                    lines.append(f"    (+{st.skipped} fora do limite MCP_MAX_TOOLS)")
            elif st.error:
                lines.append(f"  {cfg.name}: PROBLEMA ({st.error})")
            else:
                lines.append(f"  {cfg.name}: conectando...")
        return "\n".join(lines)
