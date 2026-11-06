"""Clipes pelo OBS, contra um obs-websocket 5 falso (com senha de verdade)."""

import base64
import hashlib
import json

import pytest

from akemi.actions.obs import CLIP_RE, Clipper, OBSClient, OBSError, auth_string, register_obs_actions
from akemi.actions.registry import ActionCall, ActionRegistry
from akemi.core.events import Event, Trust

PASSWORD = "senha-do-obs"


class FakeOBS:
    """Fala o protocolo do obs-websocket 5: Hello, Identify, Identified e pedidos."""

    def __init__(self, password: str = PASSWORD, buffer_on: bool = True, can_start: bool = True) -> None:
        self.password = password
        self.buffer_on = buffer_on
        self.can_start = can_start
        self.requests: list[str] = []
        self.saved = 0

    async def handler(self, ws) -> None:
        salt, challenge = "sal123", "desafio456"
        hello = {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}
        if self.password:
            hello["authentication"] = {"salt": salt, "challenge": challenge}
        await ws.send(json.dumps({"op": 0, "d": hello}))
        ident = json.loads(await ws.recv())
        assert ident["op"] == 1
        if self.password:
            secret = base64.b64encode(hashlib.sha256((self.password + salt).encode()).digest()).decode()
            want = base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()
            if ident["d"].get("authentication") != want:
                await ws.close(4009, "Authentication failed.")
                return
        await ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        async for raw in ws:
            msg = json.loads(raw)
            kind, rid = msg["d"]["requestType"], msg["d"]["requestId"]
            self.requests.append(kind)
            ok, data, comment = True, {}, None
            if kind == "GetReplayBufferStatus":
                data = {"outputActive": self.buffer_on}
            elif kind == "StartReplayBuffer":
                ok = self.can_start
                comment = None if ok else "Replay buffer is not available."
                self.buffer_on = self.buffer_on or ok
            elif kind == "SaveReplayBuffer":
                ok = self.buffer_on
                self.saved += ok
            elif kind == "GetLastReplayBufferReplay":
                data = {"savedReplayPath": r"C:\Users\m\Videos\Replay 2026-11-06 12-40-01.mkv"}
            status = {"result": ok, "code": 100 if ok else 501}
            if comment:
                status["comment"] = comment
            await ws.send(json.dumps({"op": 7, "d": {"requestType": kind, "requestId": rid,
                                                     "requestStatus": status, "responseData": data}}))


async def serve(fake: FakeOBS):
    from websockets.asyncio.server import serve as ws_serve

    server = await ws_serve(fake.handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


def test_auth_string_matches_the_spec():
    # base64(sha256(base64(sha256(senha + sal)) + desafio))
    secret = base64.b64encode(hashlib.sha256(b"supersecretpasswordsalt").digest()).decode()
    expected = base64.b64encode(hashlib.sha256((secret + "challenge").encode()).digest()).decode()
    assert auth_string("supersecretpassword", "salt", "challenge") == expected


@pytest.mark.parametrize("text", ["clipa isso", "Akemi, clipa isso!", "clipa", "salva esse clipe",
                                  "faz um clipe", "corta essa", "clipa aí"])
def test_clip_phrases(text):
    assert CLIP_RE.match(text)


@pytest.mark.parametrize("text", ["clipa o vídeo do youtube que eu te mandei ontem", "eclipse", "o clipe novo da banda"])
def test_not_clip_phrases(text):
    assert not CLIP_RE.match(text)


@pytest.mark.asyncio
async def test_clip_saves_the_replay_buffer():
    fake = FakeOBS()
    server, url = await serve(fake)
    try:
        clipper = Clipper(OBSClient(url, PASSWORD), saved_wait_s=0)
        reply = await clipper.clip()
        assert reply == "Clipei! Ficou salvo como Replay 2026-11-06 12-40-01.mkv."
        assert fake.saved == 1
        assert fake.requests == ["GetReplayBufferStatus", "SaveReplayBuffer", "GetLastReplayBufferReplay"]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_buffer_off_gets_turned_on_and_she_says_so():
    fake = FakeOBS(buffer_on=False)
    server, url = await serve(fake)
    try:
        reply = await Clipper(OBSClient(url, PASSWORD), saved_wait_s=0).clip()
        assert "Liguei agora" in reply and fake.buffer_on and fake.saved == 0
        fake2 = FakeOBS(buffer_on=False, can_start=False)
        server2, url2 = await serve(fake2)
        reply = await Clipper(OBSClient(url2, PASSWORD), saved_wait_s=0).clip()
        assert "Não consegui ligar" in reply and "Gravação de Replay" in reply
        server2.close()
        reply = await Clipper(OBSClient(url, PASSWORD), autostart=False, saved_wait_s=0).clip()
        assert fake.saved == 1  # já estava ligado pelo primeiro
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_wrong_password_and_obs_closed():
    fake = FakeOBS()
    server, url = await serve(fake)
    try:
        with pytest.raises(OBSError, match="senha"):
            await OBSClient(url, "errada").run(("GetReplayBufferStatus", None))
        with pytest.raises(OBSError, match="OBS_PASSWORD"):
            await OBSClient(url, "").run(("GetReplayBufferStatus", None))
    finally:
        server.close()
        await server.wait_closed()
    with pytest.raises(OBSError, match="não achei o OBS"):
        await OBSClient(url, PASSWORD, timeout=2).run(("GetReplayBufferStatus", None))


@pytest.mark.asyncio
async def test_only_the_owner_clips():
    fake = FakeOBS()
    server, url = await serve(fake)
    try:
        clipper = Clipper(OBSClient(url, PASSWORD), saved_wait_s=0)
        reg = ActionRegistry()
        register_obs_actions(reg, clipper)
        r = await reg.request(ActionCall("clipar", {}), Trust.PUBLIC)  # o chat da live
        assert r.status == "denied" and fake.saved == 0
        assert await clipper.shortcut("clipa isso", Event("chat.message", trust=Trust.PUBLIC)) is None
        r = await reg.request(ActionCall("clipar", {}), Trust.OWNER)
        assert r.status == "executed" and r.value.startswith("Clipei!")
        assert (await clipper.shortcut("Akemi, clipa isso!", Event("owner.message", trust=Trust.OWNER))).startswith(
            "Clipei!")
        assert fake.saved == 2
    finally:
        server.close()
        await server.wait_closed()
