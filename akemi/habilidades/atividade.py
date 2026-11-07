"""O que o dono está fazendo agora: qual programa abriu, ou disse que vai abrir.

Duas fontes:

- **Processos com janela** (no Windows, pelo PowerShell).
  Ela olha de tempos em tempos e percebe quando um programa abre ou fecha.
- **O que você diz**: "vou jogar Minecraft", "vou abrir o OBS", "vou usar o Blender".

O catálogo abaixo diz, para programas conhecidos, **por onde dá para entrar**,
da opção mais fácil para a mais difícil:

1. conexão oficial que o programa oferece (API local, websocket, RPC);
2. pasta de mods, plugins ou extensões;
3. arquivos que ele escreve (logs, saves), só para ler o que está acontecendo;
4. nada disso: só a tela, que ela já vê.

Para programas fora do catálogo, o construtor usa o que o modelo sabe e os
nomes dos arquivos da pasta do programa.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from .limites import program_key, program_slug

logger = structlog.get_logger()


@dataclass(frozen=True)
class Program:
    key: str  # chave estável: "minecraft"
    name: str  # nome bonito: "Minecraft"
    bridges: tuple[str, ...] = ()  # por onde entrar, na ordem de preferência
    exe: str = ""  # caminho do executável, quando ela achou


@dataclass(frozen=True)
class Known:
    name: str
    processes: tuple[str, ...]
    words: tuple[str, ...]  # como você chama ele numa frase
    bridges: tuple[str, ...]


CATALOG: dict[str, Known] = {
    "minecraft": Known(
        "Minecraft", ("minecraft", "minecraft.windows", "minecraftlauncher"),
        ("minecraft", "mine", "maine"),
        ("Bedrock: comando /connect do jogo liga um websocket local (lê o chat, roda comandos)",
         "Java: pasta mods/ com Fabric (a ponte já precisa vir compilada)",
         "Java: logs/latest.log mostra chat, mortes e conquistas em texto"),
    ),
    "obs": Known(
        "OBS", ("obs64", "obs"), ("obs", "live", "stream"),
        ("obs-websocket 5 em ws://127.0.0.1:4455 (cenas, fontes, gravação)",),
    ),
    "spotify": Known(
        "Spotify", ("spotify",), ("spotify", "musica", "música"),
        ("Web API do Spotify (precisa de app e token do dono)", "título da janela mostra a música tocando"),
    ),
    "discord": Known(
        "Discord", ("discord",), ("discord",),
        ("RPC local do Discord (ipc) para presença", "a Akemi já entra em call pelo modo chamada"),
    ),
    "vscode": Known(
        "VS Code", ("code",), ("vscode", "vs code", "visual studio code"),
        ("extensões do VS Code (pasta ~/.vscode/extensions)", "terminal integrado e tasks.json"),
    ),
    "blender": Known(
        "Blender", ("blender",), ("blender",),
        ("add-ons em Python (pasta scripts/addons do Blender)",),
    ),
    "stardew": Known(
        "Stardew Valley", ("stardew valley", "stardewmodapi"), ("stardew",),
        ("SMAPI: pasta Mods/ com mods em C#", "saves em %APPDATA%/StardewValley/Saves (XML)"),
    ),
    "terraria": Known(
        "Terraria", ("terraria", "tmodloader"), ("terraria",),
        ("tModLoader: mods em C#",),
    ),
    "factorio": Known(
        "Factorio", ("factorio",), ("factorio",),
        ("RCON do Factorio (comandos Lua)", "mods em Lua na pasta mods/"),
    ),
    "osu": Known(
        "osu!", ("osu!",), ("osu",),
        ("arquivos de replay e o banco osu!.db, só leitura",),
    ),
    "steam": Known(
        "Steam", ("steam", "steamwebhelper"), ("steam",),
        ("pasta steamapps/ e arquivos .acf dizem o que está instalado",),
    ),
}

_BY_PROCESS = {p: k for k, known in CATALOG.items() for p in known.processes}

GOING_TO_RE = re.compile(
    r"\b(?:vou|vamos|bora|partiu|agora vou)\s+(?:jogar|abrir|usar|editar no|editar em|mexer no|mexer em|"
    r"entrar no|entrar em|rodar)\s+(?:o |a |um |uma |no |na )?(?P<what>[\w!.' +-]{2,40}?)"
    r"(?:\s+(?:agora|ja|já|um pouco|hoje|rapidinho|aqui)\b.*)?\s*[.!?]*$",
    re.I,
)


def known_program(key: str, exe: str = "") -> Program:
    k = CATALOG.get(key)
    if k is None:
        return Program(key, key.replace("-", " ").title(), (), exe)
    return Program(key, k.name, k.bridges, exe)


def from_process(process: str, exe: str = "", title: str = "") -> Program:
    key = program_key(process)
    if key in ("java", "javaw") and "minecraft" in title.lower():
        key = "minecraft"  # o Minecraft Java roda dentro do javaw
    return known_program(_BY_PROCESS.get(key) or program_slug(key), exe)


def from_phrase(text: str) -> Program | None:
    """ "vou jogar minecraft agora" -> Minecraft. None se a frase não é isso."""
    m = GOING_TO_RE.search(text.strip())
    if not m:
        return None
    what = program_key(m.group("what")).strip(" .!?")
    if not what:
        return None
    for key, known in CATALOG.items():
        if any(re.search(rf"\b{re.escape(w)}\b", what) for w in known.words) or what == key:
            return known_program(key)
    key = program_slug(what)
    return Program(key, m.group("what").strip().title()) if key else None


# ---------------------------------------------------------------- processos
@dataclass
class Window:
    process: str
    exe: str = ""
    title: str = ""


PS_SCRIPT = (
    "Get-Process | Where-Object { $_.MainWindowHandle -ne 0 } | "
    "Select-Object ProcessName, Path, MainWindowTitle | ConvertTo-Json -Compress"
)


def parse_powershell(out: str) -> list[Window]:
    try:
        raw = json.loads(out or "[]")
    except ValueError:
        return []
    items = raw if isinstance(raw, list) else [raw]
    return [
        Window(str(i.get("ProcessName") or ""), str(i.get("Path") or ""), str(i.get("MainWindowTitle") or ""))
        for i in items if isinstance(i, dict) and i.get("ProcessName")
    ]


async def list_windows(timeout: float = 15.0) -> list[Window]:
    """Programas abertos com janela (os que você usa, não serviços). Só no Windows."""
    if sys.platform != "win32" or shutil.which("powershell") is None:
        return []
    args = ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_SCRIPT]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except (OSError, asyncio.TimeoutError):
        return []
    return parse_powershell(out.decode("utf-8", "replace"))


def folder_hint(exe: str, limit: int = 60) -> str:
    """Os nomes do que tem na pasta do programa (mods/, plugins/, logs/...), sem ler nada."""
    if not exe:
        return ""
    folder = Path(exe).parent
    try:
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in folder.iterdir())
    except OSError:
        return ""
    return ", ".join(names[:limit])


@dataclass
class ActivityWatcher:
    """Percebe quando um programa abre ou fecha."""

    on_open: Callable[[Program], Awaitable[None] | None]
    on_close: Callable[[Program], Awaitable[None] | None]
    interval_s: float = 20.0
    lister: Callable[[], Awaitable[list[Window]]] = list_windows
    running: dict[str, Program] = field(default_factory=dict)

    async def step(self) -> None:
        now: dict[str, Program] = {}
        for w in await self.lister():
            prog = from_process(w.process, w.exe, w.title)
            now.setdefault(prog.key, prog)
        opened = [p for k, p in now.items() if k not in self.running]
        closed = [p for k, p in self.running.items() if k not in now]
        self.running = now  # o que já estava aberto quando ela ligou também conta como "abriu"
        for prog in closed:
            await _maybe_await(self.on_close(prog))
        for prog in opened:
            await _maybe_await(self.on_open(prog))

    async def run(self) -> None:
        while True:
            try:
                await self.step()
            except Exception:
                logger.exception("activity watcher step failed")
            await asyncio.sleep(self.interval_s)


async def _maybe_await(value) -> None:
    if asyncio.iscoroutine(value):
        await value
