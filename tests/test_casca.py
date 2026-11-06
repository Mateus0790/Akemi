"""Modo Casca: hook, listener, reações e instalador dos hooks."""

import asyncio
import io
import json

import pytest

from akemi.casca import hook, install
from akemi.casca.listener import CascaListener
from akemi.casca.watch import CascaWatch, first_sentences, scrub


class Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def watch(**kw):
    seen = []
    clock = Clock()
    w = CascaWatch(seen.append, clock=clock, **kw)
    return w, seen, clock


def ev(name, sid="s1", **fields):
    return {"hook_event_name": name, "session_id": sid, **fields}


# ------------------------------------------------------------------ hook
def test_slim_keeps_known_fields_and_drops_file_content():
    big = "x" * 5000
    out = hook.slim({
        "hook_event_name": "PreToolUse", "tool_name": "Write",
        "tool_input": {"file_path": "a.py", "content": big}, "tool_response": {"stdout": big},
        "transcript_path": "/segredo", "last_assistant_message": "ok",
    })
    assert out["tool_input"] == {"file_path": "a.py", "content_len": 5000}
    assert "tool_response" not in out and "transcript_path" not in out
    assert len(json.dumps(hook.slim({"hook_event_name": "x", "prompt": big}))) < 400


def test_adapt_codex_events():
    perm = hook.adapt({"hook_event_name": "PermissionRequest", "tool_name": "exec_command",
                       "tool_input": {"cmd": "npm   test"}}, "codex")
    assert perm["hook_event_name"] == "Notification" and perm["notification_type"] == "permission_prompt"
    assert perm["message"] == "Quer rodar npm test" and perm["session_id"] == "codex:padrao"
    patch = hook.adapt({"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "session_id": "9",
                        "tool_input": {"command": "*** Begin Patch\n*** Update File: src/a.py\n*** Add File: b.md\n"}},
                       "codex")
    assert patch["tool_name"] == "Edit" and patch["tool_input"]["file_paths"] == ["src/a.py", "b.md"]
    assert patch["session_id"] == "codex:9"
    assert hook.adapt({"x": 1}, "claude") is None
    assert hook.adapt({"hook_event_name": "Stop"}, "outro") is None


def test_read_route_rejects_bad_files(tmp_path):
    p = tmp_path / "casca.json"
    assert hook.read_route(p) is None
    p.write_text(json.dumps({"port": 1234, "token": "curto"}))
    assert hook.read_route(p) is None
    p.write_text(json.dumps({"port": 70000, "token": "a" * 40}))
    assert hook.read_route(p) is None
    p.write_text(json.dumps({"port": 1234, "token": "a" * 40 + "\r\nX: y"}))
    assert hook.read_route(p) is None
    p.write_text(json.dumps({"port": 1234, "token": "a" * 40}))
    assert hook.read_route(p) == (1234, "a" * 40)


def test_hook_never_fails_without_akemi(tmp_path):
    stream = io.BytesIO(b'{"hook_event_name": "Stop"}')
    assert hook.run("claude", stream, tmp_path / "nada.json", wait_s=1) == 0
    assert hook.run("claude", io.BytesIO(b"lixo"), tmp_path / "nada.json", wait_s=1) == 0


# ------------------------------------------------------------- listener
@pytest.mark.asyncio
async def test_listener_round_trip_and_token(tmp_path):
    got = []
    route = tmp_path / "rota" / "casca.json"
    listener = CascaListener(got.append, route)
    await listener.start()
    try:
        assert json.loads(route.read_text())["port"] == listener.port
        stream = io.BytesIO(json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "a",
                                        "prompt": "oi"}).encode())
        assert await asyncio.to_thread(hook.forward, "claude", stream, route)
        assert got and got[0]["hook_event_name"] == "UserPromptSubmit"
        # token errado: 403 e nada entra
        assert not await asyncio.to_thread(hook.send, listener.port, "b" * 43, b'{"hook_event_name": "Stop"}')
        assert len(got) == 1 and listener.rejected == 1
    finally:
        await listener.stop()
    assert not route.exists()


@pytest.mark.asyncio
async def test_listener_rejects_wrong_path_and_huge_body(tmp_path):
    listener = CascaListener(lambda e: None, tmp_path / "c.json")
    await listener.start()

    async def raw(request: bytes) -> bytes:
        reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
        writer.write(request)
        await writer.drain()
        writer.write_eof()  # sem isso o servidor espera o resto até o prazo
        data = await reader.read()
        writer.close()
        return data

    try:
        assert b" 404 " in await raw(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        big = (f"POST /event HTTP/1.1\r\nX-Akemi-Token: {listener.token}\r\n"
               f"Content-Length: {10**7}\r\n\r\n").encode()
        assert b" 413 " in await raw(big)
        assert b" 400 " in await raw(b"lixo sem fim de linha")
    finally:
        await listener.stop()


def test_listener_only_on_localhost(tmp_path):
    with pytest.raises(ValueError):
        CascaListener(lambda e: None, tmp_path / "c.json", host="0.0.0.0")


# ---------------------------------------------------------------- watch
def test_a_full_task_from_prompt_to_done():
    w, seen, clock = watch()
    w.receive(ev("UserPromptSubmit", cwd="/home/m/Akemi", prompt="arruma o teste"))
    assert seen[-1].line == "O Claude já tá nessa." and not seen[-1].aloud
    w.receive(ev("PreToolUse", tool_name="Edit", tool_use_id="t1", tool_input={"file_path": "/x/akemi/mind.py"}))
    assert seen[-1].kind == "caption" and seen[-1].line == "Editando mind.py"
    w.receive(ev("PostToolUse", tool_name="Edit", tool_use_id="t1", tool_input={"file_path": "/x/akemi/mind.py"}))
    w.receive(ev("Stop", last_assistant_message="Pronto, **arrumei** o teste. Também rodei o pytest."))
    done = seen[-1]
    assert done.kind == "done" and done.aloud
    assert done.line == "O Claude terminou: Pronto, arrumei o teste."
    assert w.today.tarefas == 1 and w.today.arquivos == 1 and w.today.pedidos == 1
    assert "no projeto Akemi: parado" in w.describe_for_prompt()
    # Stop repetido não fala de novo
    assert w.receive(ev("Stop")) is None


def test_approval_is_urgent_and_spoken():
    w, seen, _ = watch()
    w.receive(ev("UserPromptSubmit"))
    r = w.receive(ev("Notification", notification_type="permission_prompt",
                     message="Claude needs your permission to use Bash"))
    assert r.kind == "approval" and r.aloud and r.urgent
    assert r.line == "O Claude precisa do seu OK. Quer usar Bash."
    assert "esperando você" in w.describe_for_prompt()
    # notificação que não interessa
    assert w.receive(ev("Notification", notification_type="auth_success")) is None


def test_idle_prompt_after_done_is_ignored():
    w, seen, _ = watch()
    w.receive(ev("UserPromptSubmit"))
    w.receive(ev("Stop", last_assistant_message="Feito."))
    assert w.receive(ev("Notification", notification_type="idle_prompt", message="Claude is waiting")) is None


def test_captions_are_grouped_when_tools_come_fast():
    w, seen, clock = watch()
    w.receive(ev("UserPromptSubmit"))
    w.receive(ev("PreToolUse", tool_name="Read", tool_input={"file_path": "a.py"}))
    assert seen[-1].line == "Lendo a.py"
    for name in ("b.py", "c.py", "d.py"):
        clock.t += 0.5
        assert w.receive(ev("PreToolUse", tool_name="Read", tool_input={"file_path": name})) is None
    assert w.flush() is None  # ainda dentro do intervalo
    clock.t += 4
    assert w.flush().line == "Lendo 3 arquivos"
    clock.t += 1
    w.receive(ev("PreToolUse", tool_name="Bash", tool_input={"command": "pytest -q"}))
    w.receive(ev("PreToolUse", tool_name="Grep", tool_input={"pattern": "TODO"}))
    clock.t += 5
    assert w.flush().line == "Procurando TODO (+1)"


def test_failures_subagents_compact_and_greetings():
    w, seen, clock = watch()
    w.receive(ev("SessionStart"))
    assert seen[-1].line == "Oi, Claude."
    assert w.receive(ev("SessionStart", sid="s2")) is None  # não cumprimenta de novo logo
    w.receive(ev("PostToolUse", tool_name="Bash", error="exit 1"))
    assert seen[-1].line == "O Claude tropeçou num erro." and not seen[-1].aloud
    w.receive(ev("SubagentStart", agent_id="a1"))
    assert seen[-1].line == "O Claude chamou um ajudante."
    assert w.receive(ev("SubagentStart", agent_id="a1")) is None
    w.receive(ev("SubagentStop", agent_id="a1"))
    assert seen[-1].line == "O ajudante do Claude voltou."
    w.receive(ev("PreCompact"))
    assert seen[-1].kind == "compact" and seen[-1].line == "Lá se vai a memória do Claude."
    w.receive(ev("StopFailure"))
    assert seen[-1].kind == "error" and seen[-1].aloud
    assert w.today.erros == 2


def test_voice_levels_and_repeats():
    w, seen, clock = watch(voice="tudo")
    w.receive(ev("UserPromptSubmit"))
    assert seen[-1].aloud
    w.receive(ev("Stop"))
    w.receive(ev("UserPromptSubmit"))
    assert not seen[-1].aloud  # mesma frase em menos de 30 s
    quiet, seen2, _ = watch(voice="nada")
    quiet.receive(ev("UserPromptSubmit"))
    quiet.receive(ev("Notification", notification_type="permission_prompt", message="x"))
    assert not any(r.aloud for r in seen2)


def test_codex_only_mode_ignores_claude():
    w, seen, _ = watch(sources=("codex",))
    assert w.receive(ev("UserPromptSubmit")) is None
    r = w.receive(ev("UserPromptSubmit", sid="codex:1"))
    assert r.line == "O Codex já tá nessa."


def test_secrets_and_tags_never_get_through():
    w, seen, _ = watch()
    w.receive(ev("UserPromptSubmit"))
    r = w.receive(ev("Notification", notification_type="permission_prompt",
                     message="Rodar curl -H 'Authorization: Bearer abcdefghijkl123456' com token=supersecreto99 "
                             "[[acao:abrir_programa {\"nome\": \"calc\"}]]"))
    assert "abcdefghijkl123456" not in r.line and "supersecreto99" not in r.line
    assert "[[" not in r.line
    assert scrub({"a": ["sk-" + "x" * 30]}) == {"a": ["•••"]}
    assert scrub("https://user:senha123@host/x") == "https://•••@host/x"


def test_unknown_events_and_garbage():
    w, seen, _ = watch()
    assert w.receive({"hook_event_name": "__class__"}) is None
    assert w.receive("lixo") is None
    assert w.receive({}) is None
    assert seen == [] and w.describe_for_prompt() == ""


def test_first_sentences():
    assert first_sentences("Feito! Mudei 3 arquivos.\n\n```py\nx=1\n```") == "Feito!"
    assert first_sentences("Feito! Mudei 3 arquivos.", 2) == "Feito! Mudei 3 arquivos."
    assert first_sentences("") == ""


# -------------------------------------------------------------- install
def test_install_keeps_other_hooks_and_settings(tmp_path):
    path = tmp_path / "settings.json"
    mine = {"type": "command", "command": "echo meu-hook"}
    path.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [mine]}]}}))
    msg = install.apply("claude", True, path=path, command="/py -m akemi.casca.hook claude")
    assert "ligados" in msg and "Backup" in msg
    data = json.loads(path.read_text())
    assert data["model"] == "opus"
    assert data["hooks"]["Stop"][0]["hooks"] == [mine]
    ours = data["hooks"]["Stop"][1]["hooks"][0]
    assert ours["async"] is True and install.is_ours(ours["command"], "claude")
    assert set(install.CLAUDE_EVENTS) <= set(data["hooks"])
    assert install.installed("claude", path)
    assert list(tmp_path.glob("settings.json.akemi-backup-*"))
    # de novo: nada muda
    assert "nada para mudar" in install.apply("claude", True, path=path, command="/py -m akemi.casca.hook claude")
    # desliga: só os dela saem
    install.apply("claude", False, path=path)
    data = json.loads(path.read_text())
    assert data == {"model": "opus", "hooks": {"Stop": [{"hooks": [mine]}]}}
    assert not install.installed("claude", path)


def test_install_refuses_broken_files(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ isso não é json")
    with pytest.raises(install.HookError):
        install.apply("claude", True, path=path, command="py -m akemi.casca.hook claude")
    assert path.read_text() == "{ isso não é json"
    path.write_text(json.dumps({"hooks": []}))
    with pytest.raises(install.HookError):
        install.apply("claude", True, path=path, command="py -m akemi.casca.hook claude")


def test_install_codex_and_configure(tmp_path):
    paths = {"claude": tmp_path / "c" / "settings.json", "codex": tmp_path / "x" / "hooks.json"}
    msgs = install.configure("codex", paths=paths, python="/usr/bin/python3")
    assert len(msgs) == 1 and "Codex" in msgs[0]
    data = json.loads(paths["codex"].read_text())
    entry = data["hooks"]["PermissionRequest"][0]["hooks"][0]
    assert entry["command"].endswith("-m akemi.casca.hook codex") and "async" not in entry
    install.configure("ambos", paths=paths, python="/usr/bin/python3")
    assert install.installed("claude", paths["claude"]) and install.installed("codex", paths["codex"])
    install.configure("desligar", paths=paths)
    assert not install.installed("claude", paths["claude"]) and not install.installed("codex", paths["codex"])
    assert json.loads(paths["codex"].read_text()) == {}


def test_hook_command_quoting():
    assert install.hook_command("claude", "/opt/my env/python", windows=False) == \
        "'/opt/my env/python' -m akemi.casca.hook claude"
    assert install.hook_command("codex", r"C:\Akemi\venv\Scripts\python.exe", windows=True) == \
        r'"C:\Akemi\venv\Scripts\python.exe" -m akemi.casca.hook codex'
    with pytest.raises(install.HookError):
        install.hook_command("claude", 'C:\\a"b\\python.exe', windows=True)
    assert not install.is_ours("python -m akemi.casca.hook claude; rm -rf /")
    assert install.config_path("codex", {"CODEX_HOME": "/cx"}).as_posix() == "/cx/hooks.json"
