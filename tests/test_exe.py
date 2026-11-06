"""Porta de entrada do Akemi.exe (akemi/exe.py), sem precisar do PyInstaller."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from akemi import exe
from akemi.casca.install import hook_command, is_ours


def test_home_dir_is_next_to_the_exe(tmp_path: Path) -> None:
    fake = tmp_path / "Akemi" / "Akemi.exe"
    assert exe.home_dir(env={}, executable=str(fake)) == fake.parent.resolve()


def test_akemi_home_wins(tmp_path: Path) -> None:
    assert exe.home_dir(env={"AKEMI_HOME": str(tmp_path)}, executable="x") == tmp_path


def test_first_run_creates_env_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # prepare_home faz chdir; o monkeypatch desfaz
    home = tmp_path / "casa"
    msg = exe.prepare_home(home)
    assert msg and ".env" in msg
    assert Path.cwd() == home.resolve()
    env = home / ".env"
    assert "BRAIN_PROVIDER" in env.read_text(encoding="utf-8")
    env.write_text("BRAIN_PROVIDER=echo\n", encoding="utf-8")
    assert exe.prepare_home(home) is None  # não sobrescreve o seu .env
    assert env.read_text(encoding="utf-8") == "BRAIN_PROVIDER=echo\n"


def test_default_is_the_console() -> None:
    target, argv = exe.resolve(["Akemi.exe", "--echo", "--voz"])
    assert argv == ["akemi", "--echo", "--voz"]
    assert callable(target)


@pytest.mark.parametrize("sub", sorted(exe.SUBCOMMANDS))
def test_subcommands(sub: str) -> None:
    _, argv = exe.resolve(["Akemi.exe", sub, "--help"])
    assert argv == [f"akemi-{sub}", "--help"]
    module, func = exe.SUBCOMMANDS[sub]
    assert callable(getattr(__import__(module, fromlist=[func]), func))


def test_dash_m_only_runs_akemi_modules() -> None:
    _, argv = exe.resolve(["Akemi.exe", "-m", "akemi.casca.hook", "claude"])
    assert argv == ["akemi.casca.hook", "claude"]
    with pytest.raises(SystemExit):
        exe.resolve(["Akemi.exe", "-m", "os"])
    with pytest.raises(SystemExit):
        exe.resolve(["Akemi.exe", "-m", "akemitrojan"])


def test_casca_hook_from_the_exe_is_still_recognized() -> None:
    # No .exe, sys.executable é o Akemi.exe; o hook gravado tem que continuar
    # sendo reconhecido como dela (para o "desligar" tirar só os dela).
    cmd = hook_command("claude", python=r"C:\Program Files\Akemi\Akemi.exe", windows=True)
    assert cmd == r'"C:\Program Files\Akemi\Akemi.exe" -m akemi.casca.hook claude'
    assert is_ours(cmd, "claude")
    _, argv = exe.resolve(["Akemi.exe", *cmd.split('" ', 1)[1].split()])
    assert argv == ["akemi.casca.hook", "claude"]


def test_main_returns_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    with pytest.raises(SystemExit) as exc:
        exe.main(["Akemi.exe", "chip", "--help"])
    assert exc.value.code == 0
    assert not getattr(sys, "frozen", False)
