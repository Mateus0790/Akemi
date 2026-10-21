"""Núcleo (servidor) e corpo conversando por WebSocket em localhost, com LLM falso."""

import asyncio
import json

import httpx
import pytest
import pytest_asyncio
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from akemi.brain.providers import EchoProvider
from akemi.core.config import Settings
from akemi.core.events import Event, Trust
from akemi.net import protocol as proto
from akemi.net.body import Body, BodyVoice, TokenRejected
from akemi.net.server import AkemiServer, build
from akemi.tts.base import TTSEngine

TOKEN = "segredo-de-teste-123456"


class RecordingTTS(TTSEngine):
    def __init__(self, delay=0.0):
        self.said = []
        self.delay = delay
        self.stops = 0

    async def speak(self, text):
        await asyncio.sleep(self.delay)
        self.said.append(text)

    async def stop(self):
        self.stops += 1


async def until(cond, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condição não aconteceu a tempo")
        await asyncio.sleep(0.02)


@pytest_asyncio.fixture
async def core(tmp_path):
    settings = Settings(
        _env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0,
        chat_batch_window_s=999, min_seconds_between_unprompted_speech=0, akemi_token=TOKEN,
        mic_enabled=True,  # deve ser desligado no servidor
    )
    llm = EchoProvider(lambda m, j: '{"decision": "ignore"}' if j else "Oi!")
    server = build(settings, fast=llm, deep=llm, persist=False)
    await server.akemi.start()
    ws = await server.serve("127.0.0.1", 0)
    port = next(iter(ws.sockets)).getsockname()[1]
    server.url = f"ws://127.0.0.1:{port}"
    server.http = f"http://127.0.0.1:{port}"
    yield server
    ws.close()
    await ws.wait_closed()
    await server.akemi.stop()


async def recv(conn, timeout=3.0):
    return json.loads(await asyncio.wait_for(conn.recv(), timeout))


async def hello(conn, token=TOKEN):
    await conn.send(proto.encode("hello", token=token, version=proto.VERSION))
    return await recv(conn)


def test_server_requires_long_token(tmp_path):
    settings = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path, akemi_token="curto")
    with pytest.raises(ValueError):
        build(settings, persist=False)


@pytest.mark.asyncio
async def test_bad_token_or_no_hello_is_rejected(core):
    for first in (proto.encode("hello", token="errado-errado-errado"), proto.encode("owner_message", text="oi")):
        async with connect(core.url, proxy=None) as conn:
            await conn.send(first)
            with pytest.raises(ConnectionClosed):
                await recv(conn)
            assert conn.close_code == proto.CLOSE_UNAUTHORIZED
    await asyncio.sleep(0.1)
    assert core.akemi.speaker.spoken == [] and not core.link.connected
    assert core.akemi.settings.mic_enabled is False


@pytest.mark.asyncio
async def test_owner_message_round_trip(core):
    async with connect(core.url, proxy=None) as conn:
        welcome = await hello(conn)
        assert welcome["type"] == "welcome" and welcome["paused"] is False
        await conn.send(proto.encode("owner_message", text="oi akemi"))
        say = await recv(conn)
        assert say["type"] == "say" and say["text"] == "Oi!"
        await conn.send(proto.encode("spoken", id=say["id"], status="done"))
        assert (await recv(conn))["type"] == "exchange"  # janela para responder sem o nome
    await until(lambda: core.akemi.speaker.spoken == ["Oi!"])


@pytest.mark.asyncio
async def test_owner_barge_in_sends_stop(core):
    async with connect(core.url, proxy=None) as conn:
        await hello(conn)
        await conn.send(proto.encode("owner_message", text="fala alguma coisa"))
        assert (await recv(conn))["type"] == "say"  # não confirmamos: ela "está falando"
        await until(lambda: core.akemi.speaker.speaking)
        await conn.send(proto.encode("owner_speech_started"))
        assert (await recv(conn))["type"] == "stop"
    await until(lambda: not core.akemi.speaker.speaking)


@pytest.mark.asyncio
async def test_ambient_from_body_is_public(core):
    seen = []
    core.akemi.bus.subscribe("ambient.*", seen.append)
    async with connect(core.url, proxy=None) as conn:
        await hello(conn)
        await conn.send(proto.encode("ambient", kind="screen", text="Janela: VS Code"))
        await conn.send(proto.encode("ambient", kind="owner", text="tipo inventado"))
        await conn.send("isso não é json")  # ignorado, conexão continua
        await conn.send(proto.encode("ambient", kind="audio", text="música tocando"))
        await until(lambda: len(seen) == 2)
    assert [e.kind for e in seen] == ["ambient.screen", "ambient.audio"]
    assert all(e.trust == Trust.PUBLIC for e in seen)


@pytest.mark.asyncio
async def test_without_body_she_keeps_thinking(core):
    core.akemi.owner_say("oi")
    await until(lambda: core.akemi.speaker.spoken == ["Oi!"])
    async with httpx.AsyncClient() as http:
        resp = await http.get(core.http + "/saude")
    assert resp.status_code == 200 and resp.json()["corpo_conectado"] is False


@pytest.mark.asyncio
async def test_new_body_replaces_old(core):
    async with connect(core.url, proxy=None) as old, connect(core.url, proxy=None) as new:
        await hello(old)
        await hello(new)
        with pytest.raises(ConnectionClosed):
            await recv(old)
        assert old.close_code == proto.CLOSE_REPLACED
        assert core.link.conn is not None


# ------------------------------------------------------------- corpo real
def body_for(core, tts, token=TOKEN):
    return Body(Settings(_env_file=None, akemi_token=token), core.url, token, tts)


@pytest.mark.asyncio
async def test_body_end_to_end(core):
    tts = RecordingTTS()
    body = body_for(core, tts)
    await body.start()
    link = asyncio.create_task(body.connect_forever())
    try:
        await asyncio.wait_for(body.connected.wait(), 3)
        await body.owner_say("oi")
        await until(lambda: tts.said == ["Oi!"])
        await until(lambda: core.akemi.speaker.spoken == ["Oi!"])  # núcleo recebeu "spoken"

        # Tela vista pelo corpo chega ao núcleo como PUBLIC.
        seen = []
        core.akemi.bus.subscribe("ambient.screen", seen.append)
        body.bus.publish(Event("ambient.screen", {"text": "Janela: Firefox"}, trust=Trust.PUBLIC))
        await until(lambda: len(seen) == 1)
        assert seen[0].trust == Trust.PUBLIC

        # /pausar no corpo pausa o núcleo, e o corpo para de perceber.
        await body.send("pause")
        await until(lambda: core.akemi.pause.paused and body.pause.paused)
        await body.send("resume")
        await until(lambda: not core.akemi.pause.paused and not body.pause.paused)
    finally:
        link.cancel()
        await asyncio.gather(link, return_exceptions=True)
        await body.stop()


@pytest.mark.asyncio
async def test_body_with_wrong_token_gives_up(core):
    body = body_for(core, RecordingTTS(), token="token-errado-mas-comprido")
    await body.start()
    try:
        with pytest.raises(TokenRejected):
            await asyncio.wait_for(body.connect_forever(), 5)
    finally:
        await body.stop()


@pytest.mark.asyncio
async def test_body_voice_stop_reports_dropped():
    done = []
    voice = BodyVoice(RecordingTTS(delay=1.0), lambda sid, status: done.append((sid, status)))
    voice.start()
    voice.enqueue(1, "primeira")
    voice.enqueue(2, "segunda")
    await until(lambda: voice.speaking)
    await voice.stop_all()
    await until(lambda: len(done) == 2)
    await voice.stop()
    assert sorted(done) == [(1, "interrupted"), (2, "interrupted")]
    assert voice.recent_speech and not voice.speaking


def test_protocol_validation():
    assert proto.decode('{"type": "x", "text": "' + "a" * 5000 + '"}')["text"] == "a" * proto.MAX_TEXT
    for bad in ("[]", "{}", '{"type": 1}', '{"type": "x", "text": 3}', "nope"):
        with pytest.raises(proto.ProtocolError):
            proto.decode(bad)


def test_server_class_is_exported():
    assert AkemiServer.health


# ---------------------------------------------------- ações do PC no corpo
from akemi.actions.desktop import StartApp, StartMenu, Window, register_desktop_actions  # noqa: E402
from akemi.actions.registry import ActionRegistry  # noqa: E402


class FakePC:
    def __init__(self):
        self.launched, self.closed = [], []
        self.windows = [Window(7, "Spotify Premium", "Spotify")]
        self.actions = ActionRegistry()
        menu = StartMenu(loader=lambda: [StartApp("Discord", "com.squirrel.Discord.Discord")],
                         launcher=self.launched.append)
        register_desktop_actions(self.actions, start_menu=menu, press=lambda vk, n: None,
                                 windows=lambda: self.windows, close=self.closed.append, open_url=lambda u: None)


def pc_responder(msgs, json_mode):
    if json_mode:
        return '{"decision": "ignore"}'
    last = msgs[-1].content.lower()
    if "abre" in last:
        return '[[acao:abrir_programa {"nome": "discord"}]]'
    if "fecha" in last:
        return '[[acao:fechar_programa {"nome": "Spotify"}]]'
    return "Oi!"


@pytest_asyncio.fixture
async def pc_core(tmp_path):
    settings = Settings(
        _env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0,
        chat_batch_window_s=999, min_seconds_between_unprompted_speech=0, akemi_token=TOKEN,
    )
    llm = EchoProvider(pc_responder)
    server = build(settings, fast=llm, deep=llm, persist=False)
    await server.akemi.start()
    ws = await server.serve("127.0.0.1", 0)
    server.url = f"ws://127.0.0.1:{next(iter(ws.sockets)).getsockname()[1]}"
    yield server
    ws.close()
    await ws.wait_closed()
    await server.akemi.stop()


@pytest_asyncio.fixture
async def pc_body(pc_core):
    pc, tts = FakePC(), RecordingTTS()
    body = Body(Settings(_env_file=None, akemi_token=TOKEN), pc_core.url, TOKEN, tts, actions=pc.actions)
    await body.start()
    link = asyncio.create_task(body.connect_forever())
    await asyncio.wait_for(body.connected.wait(), 3)
    yield body, pc, tts
    link.cancel()
    await asyncio.gather(link, return_exceptions=True)
    await body.stop()


@pytest.mark.asyncio
async def test_pc_action_runs_on_the_body(pc_core, pc_body):
    body, pc, tts = pc_body
    assert pc_core.akemi.actions.get("abrir_programa") is not None
    await body.owner_say("abre o discord")
    await until(lambda: tts.said == ["Abrindo Discord."])
    assert [a.name for a in pc.launched] == ["Discord"]


@pytest.mark.asyncio
async def test_close_program_confirms_through_the_body(pc_core, pc_body):
    body, pc, tts = pc_body
    await body.owner_say("fecha o spotify")
    await until(lambda: tts.said == ["Fecho Spotify? Diz sim ou não."])
    assert pc.closed == []
    await body.owner_say("sim")
    await until(lambda: len(tts.said) == 2)
    assert [w.hwnd for w in pc.closed] == [7] and tts.said[-1] == "Feito."


@pytest.mark.asyncio
async def test_pc_action_without_body(pc_core):
    pc_core.akemi.owner_say("abre o discord")
    await until(lambda: any("PC não está conectado" in s for s in pc_core.akemi.speaker.spoken))


@pytest.mark.asyncio
async def test_body_refuses_actions_outside_the_pc_list():
    pc = FakePC()
    pc.actions.register("apagar_tudo", "x")(lambda: pc.launched.append("BOOM"))
    body = Body(Settings(_env_file=None, akemi_token=TOKEN), "ws://x", TOKEN, RecordingTTS(), actions=pc.actions)
    sent = []

    async def fake_send(type_, **fields):
        sent.append((type_, fields))
        return True

    body.send = fake_send
    await body._run_action({"id": 1, "name": "apagar_tudo", "args": {}})
    await body._run_action({"id": 2, "name": "abrir_programa", "args": "nao-e-dict"})
    assert pc.launched == []
    assert [(t, f["status"]) for t, f in sent] == [("action_result", "denied"), ("action_result", "denied")]
