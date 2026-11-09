"""Microsoft Teams: a secretária responde nas suas conversas, pela API oficial (Graph).

Precisa de uma conta de trabalho ou escola (o Teams pessoal não tem API) e de um
registro de app no Entra ID, sem segredo (docs/SECRETARIA.md). Uma vez, rode
``akemi --teams-login``: ela mostra um código para você digitar no site da
Microsoft, e guarda o acesso em ``data/teams.json``.

- **Conversas 1:1** (e grupos, com ``SECRETARIA_GRUPOS``): quem te escrever
  enquanto você está ocupado recebe a resposta dela, enviada pela sua conta e
  marcada com 🤖. Quando você mesmo responde, ela sai de cena com essa pessoa.
- **Status do Teams** (``TEAMS_PRESENCA``): em reunião, numa ligação,
  apresentando ou em "Não incomodar", ela já liga o modo ocupado sozinha (sem
  falar nada, para não entrar no seu microfone). Quando você fica livre, conta
  os recados.

Busca as mensagens de tempos em tempos (``TEAMS_INTERVALO_S``): nada de porta aberta.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import httpx
import structlog

from . import Mensagem, Secretaria, assinar

logger = structlog.get_logger()

LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "offline_access User.Read Chat.Read ChatMessage.Send Presence.Read"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
TOKEN_FILE = "teams.json"

BUSY_AVAILABILITY = {"Busy", "BusyIdle", "DoNotDisturb"}
REASONS = {"InAMeeting": "numa reunião", "InACall": "numa ligação", "InAConferenceCall": "numa ligação",
           "Presenting": "apresentando"}
TAG_RE = re.compile(r"<[^>]+>")


class TeamsError(RuntimeError):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class TeamsAuthError(TeamsError):
    """O acesso venceu ou foi revogado: precisa de ``akemi --teams-login`` de novo."""


def html_to_text(body: dict | None) -> str:
    body = body or {}
    content = str(body.get("content") or "")
    if str(body.get("contentType") or "").lower() == "html":
        content = re.sub(r"<br\s*/?>|</p>", "\n", content, flags=re.I)
        content = html.unescape(TAG_RE.sub("", content))
    return " ".join(content.split())


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# ===================================================================== login
class TeamsAuth:
    """Login por código (device code) e renovação do acesso, sem segredo no app."""

    def __init__(self, client_id: str, tenant: str, path: Path, *, client: httpx.AsyncClient | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        if not client_id.strip():
            raise ValueError("faltou o TEAMS_CLIENT_ID")
        self.client_id = client_id.strip()
        self.tenant = (tenant or "organizations").strip()
        self.path = path
        self.client = client or httpx.AsyncClient(timeout=30)
        self.clock = clock
        self._access = ""
        self._expires = 0.0
        self.account = ""

    @property
    def _token_url(self) -> str:
        return f"{LOGIN}/{self.tenant}/oauth2/v2.0/token"

    def has_login(self) -> bool:
        return self.path.exists()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, refresh: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # é uma chave: só você lê
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"refresh_token": refresh, "conta": self.account, "client_id": self.client_id}, fh)
        os.replace(tmp, self.path)

    def _take(self, data: dict) -> None:
        self._access = str(data.get("access_token") or "")
        self._expires = self.clock() + float(data.get("expires_in") or 3600) - 120
        if data.get("refresh_token"):
            self._save(str(data["refresh_token"]))

    async def device_login(self, show: Callable[[str], None],
                           sleep: Callable[[float], object] = asyncio.sleep) -> str:
        """Mostra o código, espera você entrar e guarda o acesso. Devolve o nome da conta."""
        resp = await self.client.post(f"{LOGIN}/{self.tenant}/oauth2/v2.0/devicecode",
                                       data={"client_id": self.client_id, "scope": SCOPES})
        start = resp.json() if resp.content else {}
        if resp.status_code != 200 or "device_code" not in start:
            raise TeamsError(start.get("error_description") or f"a Microsoft recusou ({resp.status_code})")
        show(f"Abra {start.get('verification_uri', 'https://microsoft.com/devicelogin')} e digite o código "
             f"{start['user_code']} (vale por {int(start.get('expires_in', 900)) // 60} minutos).")
        interval = float(start.get("interval") or 5)
        deadline = self.clock() + float(start.get("expires_in") or 900)
        while self.clock() < deadline:
            await sleep(interval)
            resp = await self.client.post(self._token_url, data={
                "grant_type": DEVICE_GRANT, "client_id": self.client_id, "device_code": start["device_code"]})
            data = resp.json() if resp.content else {}
            if resp.status_code == 200 and data.get("access_token"):
                self._access = str(data["access_token"])
                me = await self.client.get(f"{GRAPH}/me", headers={"Authorization": f"Bearer {self._access}"})
                self.account = str((me.json() if me.status_code == 200 else {}).get("displayName") or "")
                self._take(data)
                return self.account
            error = data.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise TeamsError(data.get("error_description") or str(error or resp.status_code))
        raise TeamsError("o código venceu antes de você entrar")

    async def token(self, force: bool = False) -> str:
        if self._access and not force and self.clock() < self._expires:
            return self._access
        refresh = self._load().get("refresh_token")
        if not refresh:
            raise TeamsAuthError("sem login: rode akemi --teams-login")
        resp = await self.client.post(self._token_url, data={
            "grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": refresh, "scope": SCOPES})
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or not data.get("access_token"):
            if data.get("error") in ("invalid_grant", "interaction_required", "invalid_client"):
                raise TeamsAuthError(f"o acesso ao Teams venceu ({data.get('error')}): rode akemi --teams-login")
            raise TeamsError(f"renovar o acesso falhou ({resp.status_code})")
        self.account = self._load().get("conta", "")
        self._take(data)
        return self._access

    async def aclose(self) -> None:
        await self.client.aclose()


# ================================================================ conversas
class TeamsChats:
    def __init__(self, secretaria: Secretaria, auth: TeamsAuth, *, interval_s: float = 15.0,
                 presence: bool = True, groups: bool = False,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.secretaria = secretaria
        self.auth = auth
        self.interval_s = max(5.0, float(interval_s))
        self.presence = presence
        self.groups = groups
        self.now = now
        self.me_id = ""
        self.since: datetime | None = None
        self.seen: dict[str, datetime] = {}
        self._mine: deque[str] = deque(maxlen=200)  # o que ela mandou pela sua conta
        self.answered = 0

    async def _request(self, method: str, path: str, **kw) -> dict:
        for attempt in (0, 1):
            token = await self.auth.token(force=attempt == 1)
            try:
                resp = await self.auth.client.request(
                    method, f"{GRAPH}{path}", headers={"Authorization": f"Bearer {token}"}, **kw)
            except httpx.HTTPError as exc:
                raise TeamsError(f"{path} falhou ({type(exc).__name__})") from None
            if resp.status_code == 401:
                if attempt == 0:
                    continue  # acesso venceu antes da hora: renova e tenta de novo
                raise TeamsAuthError("o Teams recusou o acesso: rode akemi --teams-login", 401)
            if resp.status_code >= 300:
                raise TeamsError(f"{path}: {resp.status_code} {resp.text[:160]}", resp.status_code)
            return resp.json() if resp.content else {}
        raise TeamsAuthError("o Teams recusou o acesso: rode akemi --teams-login", 401)  # pragma: no cover

    async def _check_presence(self) -> None:
        try:
            data = await self._request("GET", "/me/presence")
        except TeamsError as exc:
            if exc.status == 403:
                logger.warning("teams presence off: no permission (Presence.Read)")
                self.presence = False
                return
            raise
        availability, activity = data.get("availability"), data.get("activity")
        busy = availability in BUSY_AVAILABILITY or activity in REASONS
        self.secretaria.set_auto_busy("teams", busy, REASONS.get(str(activity), ""))

    async def poll_once(self) -> None:
        if self.since is None:
            self.since = self.now()  # o que chegou antes de ela ligar fica com você
        if self.presence:
            await self._check_presence()
        chats = await self._request("GET", "/me/chats", params={
            "$expand": "lastMessagePreview", "$orderby": "lastMessagePreview/createdDateTime desc", "$top": "20"})
        for chat in chats.get("value") or []:
            kind = chat.get("chatType")
            if kind != "oneOnOne" and not (self.groups and kind == "group"):
                continue
            last = parse_time((chat.get("lastMessagePreview") or {}).get("createdDateTime"))
            if last is None or last <= self.seen.get(chat["id"], self.since):
                continue
            await self._read_chat(chat)

    async def _read_chat(self, chat: dict) -> None:
        cid = chat["id"]
        after = self.seen.get(cid, self.since)
        data = await self._request("GET", f"/chats/{cid}/messages", params={"$top": "10"})
        items = []
        for m in data.get("value") or []:
            when = parse_time(m.get("createdDateTime"))
            if when is not None and when > after:
                items.append((when, m))
        for when, m in sorted(items, key=lambda x: x[0]):
            self.seen[cid] = max(when, self.seen.get(cid, after))
            if m.get("messageType") != "message" or m.get("deletedDateTime") or m.get("id") in self._mine:
                continue
            user = (m.get("from") or {}).get("user") or {}
            if not user.get("id"):
                continue  # app, bot ou aviso do sistema
            contact = Mensagem(str(user.get("displayName") or "alguém")[:80], html_to_text(m.get("body")),
                               "teams", cid, grupo=chat.get("chatType") != "oneOnOne")
            if user["id"] == self.me_id:
                self.secretaria.took_over(contact)  # você respondeu: ela sai de cena
                continue
            reply = await self.secretaria.receive(contact)
            if reply:
                sent = await self._request("POST", f"/chats/{cid}/messages",
                                           json={"body": {"contentType": "text", "content": assinar(reply)}})
                if sent.get("id"):
                    self._mine.append(sent["id"])
                self.answered += 1

    async def run(self) -> None:
        """Loop supervisionado. Sem login (ou acesso revogado): desliga e avisa no log."""
        try:
            me = await self._request("GET", "/me")
        except TeamsAuthError as exc:
            logger.error("teams off", reason=str(exc))
            if "teams" in self.secretaria.canais:
                self.secretaria.canais.remove("teams")
            return
        self.me_id = str(me.get("id") or "")
        logger.info("teams connected", conta=me.get("displayName"))
        while True:
            try:
                await self.poll_once()
            except TeamsAuthError as exc:
                logger.error("teams off", reason=str(exc))
                if "teams" in self.secretaria.canais:
                    self.secretaria.canais.remove("teams")
                return
            await asyncio.sleep(self.interval_s)

    async def stop(self) -> None:
        await self.auth.aclose()
