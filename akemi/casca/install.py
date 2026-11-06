"""Liga e desliga os hooks do Modo Casca no Claude Code e no Codex.

    akemi --casca claude      # ~/.claude/settings.json
    akemi --casca codex       # ~/.codex/hooks.json
    akemi --casca ambos
    akemi --casca desligar    # tira só os hooks dela

Mantém os seus outros hooks e configurações, faz backup do arquivo antes de
mudar e recusa mexer num arquivo que não seja JSON válido. ``CLAUDE_CONFIG_DIR``
e ``CODEX_HOME`` mudam onde os arquivos ficam, como no próprio Claude Code e Codex.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CLAUDE_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
    "Notification", "Stop", "StopFailure", "SubagentStart", "SubagentStop", "PreCompact", "SessionEnd",
)
CODEX_EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse", "Stop",
    "SessionEnd", "Interrupt", "PreCompact", "SubagentStart", "SubagentStop",
)
EVENTS = {"claude": CLAUDE_EVENTS, "codex": CODEX_EVENTS}
MODES = {"claude": ("claude",), "codex": ("codex",), "ambos": ("claude", "codex"), "desligar": ()}
NAMES = {"claude": "Claude Code", "codex": "Codex"}
_OURS_RE = re.compile(r"-m\s+akemi\.casca\.hook\s+(claude|codex)\s*$")


class HookError(RuntimeError):
    """Algo no arquivo de configuração impede mexer nele com segurança."""


def config_path(source: str, env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    env = os.environ if env is None else env
    home = home or Path.home()
    if source == "codex":
        folder = env.get("CODEX_HOME", "").strip()
        return (Path(folder) if folder else home / ".codex") / "hooks.json"
    if source == "claude":
        folder = env.get("CLAUDE_CONFIG_DIR", "").strip()
        return (Path(folder) if folder else home / ".claude") / "settings.json"
    raise ValueError(f"assistente desconhecido: {source}")


def hook_command(source: str, python: str | None = None, windows: bool | None = None) -> str:
    """O comando que o assistente roda: o Python desta instalação da Akemi."""
    if source not in EVENTS:
        raise ValueError(f"assistente desconhecido: {source}")
    python = python or sys.executable
    windows = os.name == "nt" if windows is None else windows
    if not python or any(c in python for c in "\r\n\0"):
        raise HookError("não achei o caminho do Python da Akemi")
    if windows:
        if '"' in python:
            raise HookError("o caminho do Python tem aspas; não dá para montar o hook")
        exe = f'"{python}"'
    else:
        exe = shlex.quote(python)
    return f"{exe} -m akemi.casca.hook {source}"


def is_ours(command: Any, source: str | None = None) -> bool:
    if not isinstance(command, str):
        return False
    m = _OURS_RE.search(command)
    return bool(m) and (source is None or m.group(1) == source)


def _load(text: str, path: Path) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        root = json.loads(text)
    except ValueError as exc:
        raise HookError(f"{path} não é um JSON válido. Não mudei nada.") from exc
    if not isinstance(root, dict):
        raise HookError(f"{path} precisa ser um objeto JSON. Não mudei nada.")
    return root


def plan(source: str, on: bool, text: str | None, command: str, path: Path = Path("settings.json")) -> str | None:
    """O novo conteúdo do arquivo, ou None se não muda nada. Não toca no disco."""
    root = _load(text or "", path)
    hooks = root.get("hooks", {})
    if not isinstance(hooks, dict):
        raise HookError(f"{path} tem 'hooks' num formato que não conheço. Não mudei nada.")
    hooks = dict(hooks)
    for name, groups in list(hooks.items()):
        if not isinstance(groups, list):
            raise HookError(f"{path} tem hooks de {name} num formato que não conheço. Não mudei nada.")
        kept, removed = [], False
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                raise HookError(f"{path} tem um grupo de hooks inválido em {name}. Não mudei nada.")
            others = [h for h in entries if not (isinstance(h, dict) and is_ours(h.get("command"), source))]
            if len(others) == len(entries):
                kept.append(group)
                continue
            removed = True
            if others:
                kept.append({**group, "hooks": others})
        if removed:
            if kept:
                hooks[name] = kept
            else:
                hooks.pop(name)
    if on:
        for name in EVENTS[source]:
            entry: dict[str, Any] = {"type": "command", "command": command, "timeout": 10 if source == "claude" else 3}
            if source == "claude":
                entry["async"] = True  # o Claude Code não espera a Akemi
            hooks.setdefault(name, []).append({"hooks": [entry]})
    new_root = dict(root)
    if hooks:
        new_root["hooks"] = hooks
    else:
        new_root.pop("hooks", None)
    if new_root == root and text:
        return None
    if not on and not text:
        return None
    return json.dumps(new_root, ensure_ascii=False, indent=2) + "\n"


def installed(source: str, path: Path | None = None) -> bool:
    """Os hooks dela estão neste arquivo?"""
    path = path or config_path(source)
    try:
        root = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    hooks = root.get("hooks") if isinstance(root, dict) else None
    if not isinstance(hooks, dict):
        return False
    for groups in hooks.values():
        for group in groups if isinstance(groups, list) else []:
            for entry in (group.get("hooks") if isinstance(group, dict) else None) or []:
                if isinstance(entry, dict) and is_ours(entry.get("command"), source):
                    return True
    return False


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.akemi-{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def apply(source: str, on: bool, *, path: Path | None = None, command: str | None = None) -> str:
    """Liga (``on``) ou desliga os hooks de um assistente. Devolve o que fez."""
    path = path or config_path(source)
    try:
        before = path.read_text(encoding="utf-8") if path.exists() else None
    except UnicodeDecodeError as exc:
        raise HookError(f"{path} não está em UTF-8, então não sei ler. Não mudei nada.") from exc
    after = plan(source, on, before, command or hook_command(source), path)
    name = NAMES[source]
    if after is None:
        return f"{name}: nada para mudar ({'já estava ligado' if on else 'não tinha hooks meus'})."
    backup = ""
    if before is not None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        copy = path.with_name(f"{path.name}.akemi-backup-{stamp}")
        copy.write_text(before, encoding="utf-8")
        backup = f" Backup em {copy}."
    _write(path, after)
    if on:
        return f"{name}: hooks ligados em {path}.{backup} Reinicie o {name} para valer."
    return f"{name}: hooks tirados de {path}.{backup}"


def configure(mode: str, paths: Mapping[str, Path] | None = None, python: str | None = None) -> list[str]:
    """``claude`` | ``codex`` | ``ambos`` | ``desligar``."""
    if mode not in MODES:
        raise ValueError("use claude, codex, ambos ou desligar")
    wanted, paths = MODES[mode], paths or {}
    done = []
    for source in EVENTS:
        on = source in wanted
        path = paths.get(source) or config_path(source)
        if not on and not path.exists():
            continue  # nunca foi ligado: nem fala dele
        done.append(apply(source, on, path=path, command=hook_command(source, python) if on else ""))
    return done


def detect() -> tuple[str, ...]:
    """Quais assistentes estão com os hooks dela ligados."""
    return tuple(s for s in EVENTS if installed(s))


# ------------------------------------------------- modo salvo pela Akemi
MODE_FILE = "casca.json"


def save_mode(data_dir: Path, mode: str) -> None:
    """Guarda em ``data/casca.json`` o que você ligou (é o que ``CASCA=auto`` usa)."""
    path = Path(data_dir) / MODE_FILE
    if mode == "desligar":
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"modo": mode}), encoding="utf-8")


def saved_mode(data_dir: Path) -> tuple[str, ...]:
    try:
        mode = json.loads((Path(data_dir) / MODE_FILE).read_text(encoding="utf-8")).get("modo")
    except (OSError, ValueError, AttributeError):
        return ()
    return MODES.get(mode, ()) if mode != "desligar" else ()


def sources_for(setting: str, data_dir: Path) -> tuple[str, ...]:
    """Quais assistentes acompanhar, pela configuração ``CASCA``."""
    if setting == "auto":
        return saved_mode(data_dir)
    if setting == "desligado":
        return ()
    return MODES.get(setting, ())
