"""O comando que o Claude Code e o Codex rodam a cada evento (Modo Casca).

    python -m akemi.casca.hook claude    < evento.json
    python -m akemi.casca.hook codex     < evento.json

Lê o evento do stdin, deixa só o que interessa, encurta textos grandes e manda
para a Akemi em 127.0.0.1, com o token do arquivo de rota. Se a Akemi não
estiver rodando, não faz nada. **Sempre sai com código 0** e nunca escreve na
saída: um hook que falha ou fala atrapalharia o assistente.

Só biblioteca padrão, para abrir rápido.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, BinaryIO

INPUT_LIMIT = 8 * 1024 * 1024  # evento maior que isso é ignorado
BODY_LIMIT = 512 * 1024  # o que vai para a Akemi
ROUTE_LIMIT = 4096
SOURCES = ("claude", "codex")
TOKEN_HEADER = "X-Akemi-Token"

# Só estes campos vão para a Akemi. Conteúdo de arquivo editado e a saída das
# ferramentas nunca vão; do que foi editado, só o tamanho.
KEEP = (
    "hook_event_name", "session_id", "cwd", "session_title", "permission_mode", "tool_name",
    "tool_input", "tool_use_id", "duration_ms", "error", "is_interrupt", "prompt", "message",
    "title", "notification_type", "last_assistant_message", "agent_id", "agent_type", "source",
    "reason", "trigger",
)
CONTENT_KEYS = ("content", "old_string", "new_string", "new_source", "edits", "file_text")
_MISSING = object()


def route_path() -> Path:
    """Onde a Akemi deixa a porta e o token (``AKEMI_CASCA_ROTA`` muda o lugar)."""
    custom = os.environ.get("AKEMI_CASCA_ROTA", "").strip()
    return Path(custom) if custom else Path.home() / ".akemi" / "casca.json"


def cap(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _trim(value: Any, depth: int) -> Any:
    if isinstance(value, str):
        return cap(value, 280)
    if isinstance(value, (list, dict)) and depth > 2:
        return _MISSING
    if isinstance(value, list):
        kept = [_trim(v, depth + 1) for v in value[:8]]
        return [None if v is _MISSING else v for v in kept]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key in CONTENT_KEYS:
                out[f"{key}_len"] = len(item) if isinstance(item, (str, list)) else 0
                continue
            cut = _trim(item, depth + 1)
            if cut is not _MISSING:
                out[str(key)] = cut
        return out
    return value


def slim(event: dict[str, Any]) -> dict[str, Any]:
    """Só os campos conhecidos, com textos e listas encurtados."""
    out = {k: _trim(event[k], 0) for k in KEEP if k in event}
    reply = event.get("last_assistant_message")
    if isinstance(reply, str):
        out["last_assistant_message"] = cap(reply, 6000)
    return out


def _wants(tool: str, data: dict[str, Any]) -> str:
    """O que o Codex quer fazer, em palavras (os nomes das ferramentas dele não dizem nada)."""
    if tool in ("exec_command", "shell_command"):
        command = re.sub(r"\s+", " ", str(data.get("cmd") or data.get("command") or "")).strip()
        return f"Quer rodar {cap(command, 120)}" if command else "Quer rodar um comando."
    if tool == "apply_patch":
        return "Quer mudar arquivos."
    return f"Quer usar {tool}." if tool else ""


def adapt(event: Any, source: str) -> dict[str, Any] | None:
    """Põe os eventos do Codex no mesmo formato dos do Claude Code."""
    if not isinstance(event, dict) or not isinstance(event.get("hook_event_name"), str):
        return None
    if source == "claude":
        return dict(event)
    if source != "codex":
        return None
    out = dict(event)
    out["session_id"] = "codex:" + (str(event.get("session_id") or "") or "padrao")
    name, tool = event["hook_event_name"], str(event.get("tool_name") or "")
    data = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    if name == "PermissionRequest":
        out["hook_event_name"] = "Notification"
        out["notification_type"] = "permission_prompt"
        out["message"] = str(data.get("description") or "") or _wants(tool, data)
    if tool == "apply_patch":
        files = re.findall(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", str(data.get("command") or ""), re.M)
        out["tool_name"] = "Edit"
        out["tool_input"] = {"file_paths": [f.strip() for f in files]}
        if files:
            out["tool_input"]["file_path"] = files[0].strip()
    elif tool in ("exec_command", "shell_command"):
        out["tool_name"] = "Bash"
        out["tool_input"] = {**data, "command": data.get("cmd") or data.get("command") or ""}
    if name == "PreToolUse" and tool == "request_user_input":
        questions = []
        for row in data.get("questions") or []:
            if isinstance(row, dict) and (q := str(row.get("question") or row.get("title") or "")):
                questions.append(q)
        out["hook_event_name"] = "Notification"
        out["notification_type"] = "agent_needs_input"
        out["message"] = "\n".join(questions) or "O Codex quer uma resposta sua."
    return out


def read_route(path: Path) -> tuple[int, str] | None:
    """Porta e token do arquivo de rota, conferidos. None se algo estiver estranho."""
    try:
        if not path.is_file() or path.stat().st_size > ROUTE_LIMIT:
            return None
        route = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(route, dict):
        return None
    port, token = route.get("port"), route.get("token")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        return None
    if not isinstance(token, str) or not 32 <= len(token) <= 128 or not re.fullmatch(r"[\w-]+", token):
        return None
    return port, token


def send(port: int, token: str, body: bytes, timeout: float = 0.5) -> bool:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("POST", "/event", body, {"Content-Type": "application/json", TOKEN_HEADER: token})
        return conn.getresponse().status == 204
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def _read_limited(stream: BinaryIO) -> bytes | None:
    data = stream.read(INPUT_LIMIT + 1)
    return None if data is None or len(data) > INPUT_LIMIT else data


def forward(source: str, stream: BinaryIO, path: Path | None = None) -> bool:
    """Lê um evento e manda para a Akemi. Devolve se ela recebeu."""
    if source not in SOURCES:
        return False
    route = read_route(path or route_path())
    if route is None:
        return False
    raw = _read_limited(stream)
    if not raw:
        return False
    try:
        event = adapt(json.loads(raw.decode("utf-8")), source)
    except (UnicodeDecodeError, ValueError):
        return False
    if event is None:
        return False
    body = json.dumps(slim(event), ensure_ascii=False).encode("utf-8")
    if len(body) > BODY_LIMIT:
        return False
    return send(*route, body)


def run(source: str, stream: BinaryIO | None = None, path: Path | None = None, wait_s: float = 1.9) -> int:
    """Roda numa thread com prazo: o assistente nunca espera mais que ``wait_s``."""
    stream = stream or sys.stdin.buffer

    def work() -> None:
        try:
            forward(source, stream, path)
        except Exception:  # nunca atrapalha o assistente
            pass

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(wait_s)
    return 0


def main() -> None:
    source = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        code = run(source)
    except Exception:
        code = 0
    # os._exit: não espera a thread se o prazo estourou.
    sys.stdout.flush()
    os._exit(code)


if __name__ == "__main__":
    main()
