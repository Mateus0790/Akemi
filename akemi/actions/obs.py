"""Clipes pelo OBS: "clipa isso" salva os últimos segundos da sua tela.

Ideia dos clipes do Capri, feita com o OBS que você já usa na live: a Akemi manda
o OBS salvar o **replay buffer** (os últimos segundos que ele guarda na memória)
pelo obs-websocket 5, que vem junto com o OBS 28 ou mais novo.

No OBS:
1. Configurações > Saída > Gravação de Replay: ligue e escolha quantos segundos.
2. Ferramentas > Configurações do Servidor WebSocket: ligue e copie a senha.

No ``.env``: ``OBS_ENABLED=true`` e ``OBS_PASSWORD=...`` (e ``OBS_URL`` se o OBS
estiver em outro PC). Com ``OBS_REPLAY_AUTOSTART=true`` (padrão), ela liga o
replay buffer quando você pede o primeiro clipe e ele estiver desligado.

Só você pede clipe: o chat da live não consegue (seria spam de clipes).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
import json
import re
from collections.abc import Callable
from pathlib import PureWindowsPath
from typing import Any

import structlog

from akemi.core.events import Event, Trust

from .registry import ActionRegistry

logger = structlog.get_logger()

RPC_VERSION = 1
CLIP_RE = re.compile(
    r"^(?:akemi\W+)?(?:clipa|clipar|clippa|clipe isso|clip that|salva (?:o |esse |um |isso num )?clipe|"
    r"faz (?:um )?clipe|corta (?:isso|essa))\b(?:\s+(?:isso|ai|aí|essa|esse|pra mim|agora))*\W*$",
    re.I,
)


class OBSError(RuntimeError):
    """O OBS respondeu com erro ou não deu para falar com ele."""


def auth_string(password: str, salt: str, challenge: str) -> str:
    """Autenticação do obs-websocket 5: base64(sha256(base64(sha256(senha + sal)) + desafio))."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class OBSClient:
    """Uma conversa curta com o OBS: conecta, identifica, faz pedidos e fecha."""

    def __init__(self, url: str = "ws://127.0.0.1:4455", password: str = "", *, timeout: float = 5.0,
                 connect: Callable[..., Any] | None = None) -> None:
        self.url = url
        self.password = password
        self.timeout = timeout
        self._connect = connect
        self._ids = itertools.count(1)

    def _opener(self) -> Callable[..., Any]:
        if self._connect is not None:
            return self._connect
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:
            raise OBSError('falta o pacote websockets: pip install -e ".[obs]"') from exc
        return connect

    async def run(self, *requests: tuple[str, dict | None]) -> list[dict]:
        """Faz os pedidos em ordem numa conexão só. Devolve o ``responseData`` de cada um."""
        try:
            return await asyncio.wait_for(self._run(requests), self.timeout)
        except asyncio.TimeoutError as exc:
            raise OBSError("o OBS não respondeu a tempo") from exc
        except OSError as exc:
            raise OBSError("não achei o OBS (ele está aberto, com o servidor WebSocket ligado?)") from exc

    async def _run(self, requests) -> list[dict]:
        opener = self._opener()  # sem o pacote websockets, explica o que instalar
        from websockets.exceptions import ConnectionClosed

        try:
            async with opener(self.url, max_size=2**20) as ws:
                await self._identify(ws)
                return [await self._request(ws, kind, data) for kind, data in requests]
        except ConnectionClosed as exc:
            code = getattr(getattr(exc, "rcvd", None), "code", None)
            if code == 4009:
                raise OBSError("a senha do OBS (OBS_PASSWORD) não bate") from exc
            raise OBSError(f"o OBS fechou a conexão ({code})") from exc

    async def _identify(self, ws) -> None:
        hello = json.loads(await ws.recv())
        if hello.get("op") != 0:
            raise OBSError("isso não parece o obs-websocket 5")
        identify: dict[str, Any] = {"rpcVersion": RPC_VERSION, "eventSubscriptions": 0}
        challenge = (hello.get("d") or {}).get("authentication")
        if challenge:
            if not self.password:
                raise OBSError("o OBS pede senha: coloque OBS_PASSWORD no .env")
            identify["authentication"] = auth_string(self.password, challenge["salt"], challenge["challenge"])
        await ws.send(json.dumps({"op": 1, "d": identify}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("op") == 2:
                return

    async def _request(self, ws, kind: str, data: dict | None) -> dict:
        rid = f"akemi-{next(self._ids)}"
        await ws.send(json.dumps({"op": 6, "d": {"requestType": kind, "requestId": rid, "requestData": data or {}}}))
        while True:
            msg = json.loads(await ws.recv())
            d = msg.get("d") or {}
            if msg.get("op") == 7 and d.get("requestId") == rid:
                status = d.get("requestStatus") or {}
                if not status.get("result"):
                    raise OBSError(status.get("comment") or f"o OBS recusou {kind} ({status.get('code')})")
                return d.get("responseData") or {}


class Clipper:
    def __init__(self, client: OBSClient, *, autostart: bool = True, saved_wait_s: float = 1.5) -> None:
        self.client = client
        self.autostart = autostart
        self.saved_wait_s = saved_wait_s
        self.last_path = ""

    async def clip(self) -> str:
        """Salva o replay buffer e diz onde ficou."""
        status = (await self.client.run(("GetReplayBufferStatus", None)))[0]
        if not status.get("outputActive"):
            if not self.autostart:
                return "O replay buffer do OBS está desligado. Liga ele (ou deixa OBS_REPLAY_AUTOSTART=true) que eu clipo."
            try:
                await self.client.run(("StartReplayBuffer", None))
            except OBSError as exc:
                return (f"Não consegui ligar o replay buffer do OBS: {exc}. Veja se a Gravação de Replay "
                        "está ativada em Configurações > Saída.")
            return ("O replay buffer do OBS estava desligado, então não tinha nada para salvar. Liguei agora: "
                    "do próximo clipe em diante funciona.")
        await self.client.run(("SaveReplayBuffer", None))
        await asyncio.sleep(self.saved_wait_s)  # o OBS grava o arquivo em segundo plano
        try:
            last = (await self.client.run(("GetLastReplayBufferReplay", None)))[0]
            self.last_path = str(last.get("savedReplayPath") or "")
        except OBSError:
            self.last_path = ""
        name = PureWindowsPath(self.last_path).name if self.last_path else ""
        return f"Clipei! Ficou salvo como {name}." if name else "Clipei! Tá na pasta de gravações do OBS."

    async def shortcut(self, text: str, event: Event) -> str | None:
        """Para a mente: "clipa isso" é na hora, sem esperar o modelo (o momento passa)."""
        if event.trust < Trust.OWNER or not CLIP_RE.match(text.strip()):
            return None
        try:
            return await self.clip()
        except OBSError as exc:
            return f"Não deu para clipar: {exc}."


def register_obs_actions(registry: ActionRegistry, clipper: Clipper) -> None:
    @registry.register(
        "clipar", "salva os últimos segundos da tela num clipe (replay buffer do OBS)", speak_result=True,
        example=("clipa isso!", "[[acao:clipar]]"),
    )
    async def clipar() -> str:
        try:
            return await clipper.clip()
        except OBSError as exc:
            raise ValueError(str(exc)) from exc
