"""Porta de entrada do ``Akemi.exe`` (PyInstaller).

Um executável só para tudo:

    Akemi.exe                    # conversa pelo terminal (igual a ``akemi``)
    Akemi.exe --voz --mic        # as mesmas opções do ``akemi``
    Akemi.exe corpo --voz --mic  # igual a ``akemi-corpo``
    Akemi.exe servidor           # igual a ``akemi-servidor``
    Akemi.exe chip exportar x    # igual a ``akemi-chip``
    Akemi.exe assinatura         # igual a ``akemi-assinatura``: oficial ou modificada?
    Akemi.exe debug              # igual a ``akemi-debug``

Também aceita ``Akemi.exe -m akemi.casca.hook claude``, que é o comando que o
Modo Casca grava nos hooks do Claude Code/Codex (ele usa ``sys.executable``,
que no .exe é o próprio ``Akemi.exe``).

A pasta de trabalho vira a pasta do .exe (ou ``AKEMI_HOME``), para o ``.env``
e a pasta ``data/`` ficarem ao lado dele, mesmo abrindo por um atalho.
"""

from __future__ import annotations

import os
import runpy
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

SUBCOMMANDS = {
    "corpo": ("akemi.net.body", "main"),
    "servidor": ("akemi.net.server", "main"),
    "chip": ("akemi.storage.chip", "main"),
    "assinatura": ("akemi.assinatura", "main"),
    "debug": ("akemi.debug", "main"),
}


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def home_dir(env: dict[str, str] | None = None, executable: str | None = None) -> Path:
    """Onde ficam o ``.env`` e a pasta ``data/``."""
    env = os.environ if env is None else env  # type: ignore[assignment]
    custom = (env.get("AKEMI_HOME") or "").strip()
    if custom:
        return Path(custom).expanduser()
    return Path(executable or sys.executable).resolve().parent


def bundled(name: str) -> Path:
    """Arquivo que foi junto dentro do .exe."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / name


def prepare_home(home: Path) -> str | None:
    """Entra na pasta da Akemi e cria o ``.env`` na primeira vez."""
    home.mkdir(parents=True, exist_ok=True)
    os.chdir(home)
    env_file = home / ".env"
    example = bundled(".env.example")
    if env_file.exists() or not example.exists():
        return None
    shutil.copyfile(example, env_file)
    return (
        f"Primeira vez: criei {env_file}.\n"
        "Abra esse arquivo no Bloco de Notas, coloque a sua chave (NVIDIA_API_KEY)\n"
        "e abra a Akemi de novo. Para testar sem chave: Akemi.exe --echo\n"
    )


def resolve(argv: list[str]) -> tuple[Callable[[], object], list[str]]:
    """Escolhe o que rodar e o ``sys.argv`` que ele vai ver."""
    if len(argv) >= 3 and argv[1] == "-m":
        module = argv[2]
        if module != "akemi" and not module.startswith("akemi."):
            raise SystemExit(f"Este executável só roda módulos da Akemi (pediu {module}).")
        return (lambda: runpy.run_module(module, run_name="__main__", alter_sys=True)), [module, *argv[3:]]
    if len(argv) >= 2 and argv[1] in SUBCOMMANDS:
        module, func = SUBCOMMANDS[argv[1]]
        name = f"akemi-{argv[1]}"

        def call() -> object:
            mod = __import__(module, fromlist=[func])
            return getattr(mod, func)()

        return call, [name, *argv[2:]]

    def console() -> object:
        from akemi.console import main as console_main

        return console_main()

    return console, ["akemi", *argv[1:]]


def _pause_if_double_clicked() -> None:
    """Aberto pelo Explorer, a janela fecharia antes de dar para ler o erro."""
    if os.name != "nt" or not frozen() or not sys.stdin or not sys.stdin.isatty():
        return
    try:
        import ctypes

        processes = (ctypes.c_uint * 4)()
        alone = ctypes.windll.kernel32.GetConsoleProcessList(processes, 4) <= 1  # type: ignore[attr-defined]
    except Exception:
        alone = False
    if alone:
        try:
            input("\nAperte Enter para fechar...")
        except (EOFError, KeyboardInterrupt):
            pass


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv if argv is None else argv)
    if frozen():
        first_run = prepare_home(home_dir())
        if first_run and len(argv) == 1:
            print(first_run)
            _pause_if_double_clicked()
            return
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
            except Exception:
                pass
    target, sys.argv = resolve(argv)
    code: object = 0
    try:
        target()
    except SystemExit as exc:
        code = exc.code
    except KeyboardInterrupt:
        code = 0
    except Exception:
        import traceback

        traceback.print_exc()
        code = 1
    if code not in (0, None):
        if isinstance(code, str):
            print(code, file=sys.stderr)
            code = 1
        _pause_if_double_clicked()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
