"""O app do celular: conecta ao lado do PC e ela responde onde você falou por último."""

import asyncio
import json

import httpx
import pytest
import pytest_asyncio
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from akemi.brain.providers import EchoProvider
from akemi.core.config import Settings
from akemi.core.events import Event
from akemi.core.orchestrator import Akemi
from akemi.net import protocol as proto
from akemi.net.companion import CompanionHub, FocusVoice
from akemi.net.server import build
from akemi.tts.base import TTSEngine

TOKEN = "segredo-de-teste-123456"


class RecordingTTS(TTSEngine):
    def __init__(self):
        self.said = []

    async def speak(self, text):
        self.said.append(text)


async def until(cond, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condição não aconteceu a tempo")
        await asyncio.sleep(0.02)


async def recv(conn, timeout=3.0):
    return json.loads(await asyncio.wait_for(conn.recv(), timeout))


async def recv_type(conn, type_, timeout=3.0):
    """Pula mensagens de outros tipos até achar ``type_``."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        msg = await recv(conn, max(0.05, deadline - asyncio.get_running_loop().time()))
        if msg["type"] == type_:
            return msg


async def phone_hello(conn, token=TOKEN):
    await conn.send(proto.encode(
        "hello", token=token, version=proto.VERSION,
        corpo={"nome": "Galaxy do Mateus", "tipo": "celular", "sentidos": ["voz", "microfone"]},
    ))
    return await recv(conn)


async def pc_hello(conn):
    await conn.send(proto.encode("hello", token=TOKEN, version=proto.VERSION, corpo={"nome": "pc", "sentidos": ["voz"]}))
    return await recv(conn)


def responder(messages, json_mode):
    if json_mode:
        return '{"decision": "ignore"}'
    last = messages[-1].content
    return f"Resposta: {last.split()[-1]}"


@pytest_asyncio.fixture
async def core(tmp_path):
    settings = Settings(
        _env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0,
        chat_batch_window_s=999, min_seconds_between_unprompted_speech=0, akemi_token=TOKEN,
    )
    llm = EchoProvider(responder)
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


@pytest.mark.asyncio
async def test_phone_connects_beside_pc_and_gets_the_reply(core):
    async with connect(core.url, proxy=None) as pc, connect(core.url, proxy=None) as phone:
        await pc_hello(pc)
        welcome = await phone_hello(phone)
        assert welcome["type"] == "welcome" and welcome["nome"] == "Akemi"
        assert core.link.connected and core.phone.connected  # o PC não caiu

        await phone.send(proto.encode("owner_message", text="oi celular"))
        say = await recv_type(phone, "say")
        assert say["text"] == "Resposta: celular"
        await phone.send(proto.encode("spoken", id=say["id"], status="done"))
        await until(lambda: core.akemi.speaker.spoken == ["Resposta: celular"])
        assert "celular" in core.akemi.mind._context_block()

        # Agora fala pelo PC: a resposta sai no PC e o celular só recebe a cópia.
        await pc.send(proto.encode("owner_message", text="oi computador"))
        say_pc = await recv_type(pc, "say")
        assert say_pc["text"] == "Resposta: computador"
        await pc.send(proto.encode("spoken", id=say_pc["id"], status="done"))
        fala = await recv_type(phone, "fala")
        assert fala["text"] == "Resposta: computador"

        async with httpx.AsyncClient() as http:
            health = (await http.get(core.http + "/saude")).json()
        assert health["celular"]["conectado"] and health["corpo_conectado"]


@pytest.mark.asyncio
async def test_phone_with_wrong_token_is_rejected(core):
    async with connect(core.url, proxy=None) as phone:
        await phone.send(proto.encode("hello", token="errado-errado-errado", corpo={"tipo": "celular"}))
        with pytest.raises(ConnectionClosed):
            await recv(phone)
        assert phone.close_code == proto.CLOSE_UNAUTHORIZED
    assert not core.phone.connected


@pytest.mark.asyncio
async def test_welcome_brings_recent_history(core):
    core.akemi.owner_say("primeira")
    await until(lambda: len(core.akemi.mind.history) == 2)
    async with connect(core.url, proxy=None) as phone:
        welcome = await phone_hello(phone)
    assert welcome["historico"] == [
        {"de": "voce", "texto": "primeira"}, {"de": "akemi", "texto": "Resposta: primeira"},
    ]


@pytest.mark.asyncio
async def test_phone_disconnect_returns_voice_to_pc(core):
    async with connect(core.url, proxy=None) as pc:
        await pc_hello(pc)
        async with connect(core.url, proxy=None) as phone:
            await phone_hello(phone)
            await phone.send(proto.encode("owner_message", text="oi"))
            await recv_type(phone, "say")
        await until(lambda: not core.phone.connected)
        assert not core.phone.focused
        core.akemi.bus.publish(Event("speech.say", {"text": "Lembrete: água"}, source="lembrete"))
        say = await recv_type(pc, "say")
        assert say["text"] == "Lembrete: água"


@pytest.mark.asyncio
async def test_local_mode_with_chip_on_the_pc(tmp_path):
    """``akemi --celular``: a Akemi roda no PC e o celular conecta nela."""
    settings = Settings(
        _env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0,
        chat_batch_window_s=999, akemi_token=TOKEN,
    )
    llm = EchoProvider(responder)
    tts = RecordingTTS()
    akemi = Akemi(settings, fast=llm, deep=llm, tts=tts, persist=False, desktop_actions=False)
    await akemi.start()
    hub = CompanionHub(akemi, TOKEN)
    hub.install()
    assert isinstance(akemi.speaker.engine, FocusVoice)
    ws = await hub.serve("127.0.0.1", 0)
    url = f"ws://127.0.0.1:{next(iter(ws.sockets)).getsockname()[1]}"
    try:
        async with connect(url, proxy=None) as other:  # um "PC" não entra no modo local
            with pytest.raises(ConnectionClosed):
                await pc_hello(other)
            assert other.close_code == proto.CLOSE_BAD_MESSAGE
        async with connect(url, proxy=None) as phone:
            await phone_hello(phone)
            await phone.send(proto.encode("owner_message", text="oi celular"))
            say = await recv_type(phone, "say")
            await phone.send(proto.encode("spoken", id=say["id"], status="done"))
            assert tts.said == []  # não falou na caixa de som do PC
            akemi.owner_say("oi terminal")
            fala = await recv_type(phone, "fala")
            assert fala["text"] == "Resposta: terminal"
            await until(lambda: tts.said == ["Resposta: terminal"])
    finally:
        ws.close()
        await ws.wait_closed()
        await akemi.stop()
