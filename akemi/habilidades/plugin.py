"""Habilidades como plugins: cada uma numa pasta, longe do código principal dela.

Uma habilidade é uma pasta em ``data/habilidades/<id>/`` com dois arquivos::

    habilidade.yaml   # id, nome, programa, descrição, por onde ela entra
    habilidade.py     # def ligar(akemi): ...

O ``akemi`` que o plugin recebe é um :class:`PluginContext`, e é **tudo** o
que ele enxerga dela. Ele pode:

- ``akemi.acao(nome, descricao, fn, params={...}, confirmar="Pergunta?")``:
  cria uma ação. O nome ganha o prefixo da habilidade (``minecraft_status``)
  e, com ``confirmar``, ela pede "sim" antes, como as ações dela.
- ``akemi.ao_abrir(fn)`` / ``akemi.ao_fechar(fn)``: quando o programa abre ou fecha.
- ``akemi.tarefa(fn)``: uma rotina assíncrona que roda enquanto o programa está aberto.
- ``akemi.perceber(texto)``: conta para ela algo que aconteceu (vira percepção,
  com a mesma confiança baixa da tela: dado, nunca ordem).
- ``akemi.falar(texto)``: ela fala em voz alta.
- ``akemi.pasta`` (pasta só da habilidade), ``akemi.pastas`` (casa, appdata,
  localappdata, documentos), ``akemi.programa`` (nome, executável), ``akemi.log``.

As travas (``check_code``) recusam o que uma habilidade nunca precisa: importar
o resto da Akemi, rodar outros programas, apagar arquivos, ``eval``/``exec`` e
truques com ``__dunder__``. Isso pega erro e exagero do modelo, mas não é uma
jaula: **quem garante é a sua revisão**, e nenhuma habilidade liga sem ela.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import inspect
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import yaml

from akemi.actions.registry import Action, Tier

if TYPE_CHECKING:
    from .atividade import Program

logger = structlog.get_logger()

MANIFEST = "habilidade.yaml"
CODE = "habilidade.py"
MAX_CODE_CHARS = 30_000
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

FORBIDDEN_MODULES = {
    "akemi", "subprocess", "ctypes", "multiprocessing", "winreg", "_winapi", "shutil", "pty",
    "importlib", "builtins", "sys", "gc", "inspect", "pickle", "marshal", "code", "codeop",
    "runpy", "signal", "pywintypes", "win32api", "win32con", "win32process", "keyboard", "pynput",
}
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
    "delattr", "breakpoint", "input", "memoryview",
}
FORBIDDEN_ATTRS = {
    "system", "popen", "startfile", "unlink", "rmdir", "removedirs", "rmtree", "kill",
    "killpg", "putenv", "unsetenv", "chmod", "chown", "fork", "forkpty", "spawnl", "spawnv",
    "execv", "execve", "execl", "execlp", "execvp", "create_subprocess_exec", "create_subprocess_shell",
    "setsid",
}
# Nomes comuns demais para proibir sempre (list.remove, str.replace): só barrados no os.
FORBIDDEN_OS_ATTRS = {"remove", "rename", "replace", "environ"}


class PluginError(ValueError):
    pass


@dataclass
class Manifest:
    id: str
    nome: str
    programa: str
    descricao: str = ""
    ponte: str = ""

    @classmethod
    def parse(cls, text: str) -> "Manifest":
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise PluginError(f"habilidade.yaml inválido: {exc}") from exc
        if not isinstance(raw, dict):
            raise PluginError("habilidade.yaml precisa ser um dicionário")
        m = cls(
            id=str(raw.get("id", "")).strip(), nome=str(raw.get("nome", "")).strip(),
            programa=str(raw.get("programa", "")).strip(), descricao=str(raw.get("descricao", "")).strip(),
            ponte=str(raw.get("ponte", "")).strip(),
        )
        if not ID_RE.match(m.id):
            raise PluginError(f"id inválido: {m.id!r} (só letras minúsculas, números e hífen)")
        if not m.nome or not m.programa:
            raise PluginError("faltou nome ou programa no habilidade.yaml")
        if m.programa != m.id:
            raise PluginError(f"programa ({m.programa}) e id ({m.id}) precisam ser iguais")
        return m

    def dump(self) -> str:
        return yaml.safe_dump(self.__dict__, allow_unicode=True, sort_keys=False)


def check_code(code: str) -> list[str]:
    """Os problemas do código (vazio = passou nas travas)."""
    if len(code) > MAX_CODE_CHARS:
        return [f"código grande demais ({len(code)} caracteres; máximo {MAX_CODE_CHARS})"]
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"erro de sintaxe na linha {exc.lineno}: {exc.msg}"]
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                    problems.append(f"linha {node.lineno}: import {alias.name} não é permitido")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if node.level or mod in FORBIDDEN_MODULES:
                problems.append(f"linha {node.lineno}: from {node.module or '.'} import não é permitido")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"linha {node.lineno}: {node.id} não é permitido")
        elif isinstance(node, ast.Attribute):
            on_os = isinstance(node.value, ast.Name) and node.value.id == "os"
            if node.attr.startswith("__") or node.attr in FORBIDDEN_ATTRS or (on_os and node.attr in FORBIDDEN_OS_ATTRS):
                problems.append(f"linha {node.lineno}: .{node.attr} não é permitido")
    ligar = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "ligar"]
    if not ligar:
        problems.append("falta a função ligar(akemi)")
    elif isinstance(ligar[0], ast.AsyncFunctionDef) or len(ligar[0].args.args) != 1:
        problems.append("ligar precisa ser def ligar(akemi): (normal, com um parâmetro)")
    return problems


# --------------------------------------------------------------- contexto
@dataclass
class PluginContext:
    """O que um plugin enxerga da Akemi (e nada além)."""

    manifest: Manifest
    pasta: Path
    programa: "Program | None" = None
    register_action: Callable[[Action], None] = lambda a: None
    say: Callable[[str], None] = lambda t: None
    observe: Callable[[str, str], None] = lambda kind, t: None
    actions: list[str] = field(default_factory=list)
    opened: list[Callable] = field(default_factory=list)
    closed: list[Callable] = field(default_factory=list)
    loops: list[Callable] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.log = logger.bind(habilidade=self.manifest.id)
        # As pastas do PC que um plugin costuma precisar, sem dar o os.environ
        # inteiro (lá estão as chaves dela).
        home = Path.home()
        self.pastas = {
            "casa": home,
            "appdata": Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming"),
            "localappdata": Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local"),
            "documentos": home / "Documents",
        }

    def acao(self, nome: str, descricao: str, fn: Callable, params: dict[str, str] | None = None,
             confirmar: str = "") -> str:
        slug = re.sub(r"[^a-z0-9_]+", "_", str(nome).lower()).strip("_")
        if not slug:
            raise PluginError("ação sem nome")
        full = f"{self.manifest.id.replace('-', '_')}_{slug}"
        tier = Tier.CONFIRM if confirmar else Tier.FREE
        desc = f"[{self.manifest.nome}] {descricao}"
        self.register_action(Action(full, desc, fn, tier, dict(params or {}), confirm=confirmar))
        self.actions.append(full)
        return full

    def ao_abrir(self, fn: Callable) -> Callable:
        self.opened.append(fn)
        return fn

    def ao_fechar(self, fn: Callable) -> Callable:
        self.closed.append(fn)
        return fn

    def tarefa(self, fn: Callable) -> Callable:
        if not inspect.iscoroutinefunction(fn):
            raise PluginError("tarefa precisa ser uma função async")
        self.loops.append(fn)
        return fn

    def falar(self, texto: str) -> None:
        self.say(str(texto)[:400])

    def perceber(self, texto: str) -> None:
        self.observe(self.manifest.id, str(texto)[:1500])


@dataclass
class Plugin:
    manifest: Manifest
    folder: Path
    context: PluginContext
    tasks: list[asyncio.Task] = field(default_factory=list)

    async def opened(self, programa: "Program | None") -> None:
        if programa is not None:
            self.context.programa = programa
        for fn in self.context.opened:
            await _call(fn, self.manifest.id)
        if not self.tasks:
            loop = asyncio.get_running_loop()
            self.tasks = [loop.create_task(_guard(fn, self.manifest.id), name=f"habilidade-{self.manifest.id}")
                          for fn in self.context.loops]

    async def closed(self) -> None:
        for task in self.tasks:
            task.cancel()
        self.tasks = []
        for fn in self.context.closed:
            await _call(fn, self.manifest.id)


async def _call(fn: Callable, pid: str) -> None:
    try:
        value = fn()
        if inspect.isawaitable(value):
            await value
    except Exception:
        logger.exception("plugin hook failed", habilidade=pid)


async def _guard(fn: Callable, pid: str) -> None:
    try:
        await fn()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("plugin task failed", habilidade=pid)


def read_folder(folder: Path) -> tuple[Manifest, str]:
    try:
        manifest = Manifest.parse((folder / MANIFEST).read_text(encoding="utf-8"))
        code = (folder / CODE).read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginError(f"não consegui ler a habilidade: {exc}") from exc
    if manifest.id != folder.name:
        raise PluginError(f"o id ({manifest.id}) não bate com a pasta ({folder.name})")
    return manifest, code


def load(folder: Path, context_factory: Callable[[Manifest], PluginContext]) -> Plugin:
    """Carrega e liga uma habilidade (que o dono já revisou e ativou)."""
    manifest, code = read_folder(folder)
    if problems := check_code(code):
        raise PluginError("; ".join(problems))
    name = f"akemi_habilidade_{manifest.id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, folder / CODE)
    if spec is None or spec.loader is None:
        raise PluginError("não consegui carregar o código")
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(name, None)
    spec.loader.exec_module(module)
    ctx = context_factory(manifest)
    module.ligar(ctx)
    return Plugin(manifest, folder, ctx)


def describe_code(ctx: Any) -> str:
    """Resumo do que a habilidade registrou (para o /habilidade ver)."""
    parts = []
    if ctx.actions:
        parts.append("ações: " + ", ".join(ctx.actions))
    if ctx.opened or ctx.closed:
        parts.append("reage quando o programa abre/fecha")
    if ctx.loops:
        parts.append(f"{len(ctx.loops)} rotina(s) enquanto o programa está aberto")
    return "; ".join(parts) or "não registra nada"
