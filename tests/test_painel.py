"""Painel: configurar pelo navegador, e ela fica triste quando desligam algo dela."""

import asyncio
import json
import urllib.error
import urllib.request

import pytest

from akemi.brain.providers import EchoProvider
from akemi.core.config import Settings
from akemi.core.orchestrator import Akemi
from akemi.painel import PanelChoices, PanelError, PanelServer, coerce, load_token
from akemi.tts.base import TTSEngine


# ---------------------------------------------------------------- valores
def test_only_listed_keys_can_change():
    assert coerce("vida_enabled", False) is False
    assert coerce("vida_enabled", "true") is True
    assert coerce("owner_name", "  Ana   Clara ") == "Ana Clara"
    assert coerce("memory_keep_days", "10") == 10.0
    for key, value in (("nvidia_api_key", "x"), ("github_token", "x"), ("oficina_repo_dir", "/"),
                       ("vida_enabled", "talvez"), ("memory_keep_days", 0), ("owner_name", "")):
        with pytest.raises(PanelError):
            coerce(key, value)


def test_choices_persist_and_win_over_env(tmp_path):
    path = tmp_path / "painel.json"
    PanelChoices(path).update({"initiative_enabled": False, "owner_name": "Ana"})
    settings = Settings(_env_file=None, initiative_enabled=True)
    PanelChoices(path).apply(settings)
    assert settings.initiative_enabled is False and settings.owner_name == "Ana"


def test_bad_choices_file_is_ignored(tmp_path):
    path = tmp_path / "painel.json"
    path.write_text(json.dumps({"vida_enabled": False, "nvidia_api_key": "roubada"}))
    assert PanelChoices(path).values == {"vida_enabled": False}
    path.write_text("{quebrado")
    assert PanelChoices(path).values == {}


def test_token_is_kept_or_comes_from_akemi_token(tmp_path):
    first = load_token(tmp_path)
    assert len(first) >= 16 and load_token(tmp_path) == first
    assert load_token(tmp_path, "um-token-do-servidor-bem-longo") == "um-token-do-servidor-bem-longo"


# --------------------------------------------------------------------- HTTP
def _request(url, *, token=None, body=None, host=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if token:
        req.add_header("X-Akemi-Token", token)
    if data:
        req.add_header("Content-Type", "application/json")
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, res.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


@pytest.fixture
def server():
    saved = []
    panel = PanelServer(lambda: {"humor": "neutra"}, lambda ch: saved.append(ch) or {"ok": True},
                        token="token-de-teste-123456", port=0)
    panel.start()
    yield panel, saved, f"http://127.0.0.1:{panel.port}"
    panel.stop()


def test_page_is_public_but_api_needs_token(server):
    panel, saved, base = server
    status, html = _request(base + "/")
    assert status == 200 and "Painel da Akemi" in html and panel.token not in html
    assert _request(base + "/api/estado")[0] == 401
    assert _request(base + "/api/estado", token="errado")[0] == 401
    status, body = _request(base + "/api/estado", token=panel.token)
    assert status == 200 and json.loads(body) == {"humor": "neutra"}
    assert _request(base + "/api/salvar", body={"mudancas": {"vida_enabled": False}})[0] == 401
    assert saved == []


def test_save_and_reject_bad_requests(server):
    panel, saved, base = server
    status, _ = _request(base + "/api/salvar", token=panel.token, body={"mudancas": {"vida_enabled": False}})
    assert status == 200 and saved == [{"vida_enabled": False}]
    assert _request(base + "/api/salvar", token=panel.token, body={"mudancas": {}})[0] == 400
    assert _request(base + "/api/salvar", token=panel.token, body=["x"])[0] == 400


def test_dns_rebinding_is_refused(server):
    panel, _, base = server
    assert _request(base + "/api/estado", token=panel.token, host="evil.example:80")[0] == 403


def test_outside_the_pc_needs_a_long_token():
    with pytest.raises(PanelError):
        PanelServer(dict, dict, token="curto", host="0.0.0.0")


# ---------------------------------------------------------- com a Akemi toda
class RecordingTTS(TTSEngine):
    def __init__(self):
        self.said = []

    async def speak(self, text):
        self.said.append(text)


def _akemi(tmp_path, responder=None, **kw):
    settings = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0,
                        chat_batch_window_s=999, min_seconds_between_unprompted_speech=0, painel_port=0, **kw)
    llm = EchoProvider(responder or (lambda msgs, json_mode: "Poxa, vou sentir falta disso."))
    tts = RecordingTTS()
    return Akemi(settings, fast=llm, deep=llm, tts=tts, persist=False), tts


async def _settle(akemi):
    await akemi.bus.drain()
    await asyncio.sleep(0.3)


@pytest.mark.asyncio
async def test_she_gets_sad_when_a_feature_is_turned_off(tmp_path):
    akemi, tts = _akemi(tmp_path)
    await akemi.start()
    await _settle(akemi)
    assert akemi.state.features["initiative_enabled"] is True and tts.said == []  # 1ª vez: só aprende

    result = await akemi.apply_panel({"initiative_enabled": False})
    await _settle(akemi)
    assert akemi.state.mood == "triste" and akemi.mind.initiative is False
    assert tts.said == ["Poxa, vou sentir falta disso."]
    assert "Poxa" in result["aviso"] and "Iniciativa" in result["estado"]["desligadas"]
    assert "Iniciativa" in akemi._features_for_prompt()

    await akemi.apply_panel({"initiative_enabled": False})  # mesma coisa de novo: não repete
    await akemi.apply_panel({"initiative_enabled": True})
    await _settle(akemi)
    await akemi.stop()
    assert akemi.state.mood == "animada" and len(tts.said) == 2


@pytest.mark.asyncio
async def test_she_notices_what_changed_while_she_was_off(tmp_path):
    akemi, tts = _akemi(tmp_path, vida_enabled=False)
    akemi.state.features = {**akemi._current_features(), "vida_enabled": True}  # da última vez estava ligada
    assert akemi.mind.vida is None and akemi.actions.get("listar_combinados") is None
    await akemi.start()
    await _settle(akemi)
    await akemi.stop()
    assert akemi.state.mood == "triste" and tts.said


@pytest.mark.asyncio
async def test_feeling_never_runs_actions_and_falls_back(tmp_path):
    akemi, tts = _akemi(tmp_path, responder=lambda m, j: '[[acao:criar_tarefa {"descricao": "x"}]]')
    await akemi.start()
    await _settle(akemi)
    await akemi.apply_panel({"vida_enabled": False})
    await _settle(akemi)
    await akemi.stop()
    assert akemi.state.open_tasks == []
    assert tts.said and "Acompanhar sua vida" in tts.said[0]  # frase de reserva


@pytest.mark.asyncio
async def test_some_changes_wait_for_restart_and_bad_keys_change_nothing(tmp_path):
    akemi, _ = _akemi(tmp_path)
    await akemi.start()
    result = await akemi.apply_panel({"oficina_enabled": True, "memory_keep_days": 3})
    assert "Oficina" in result["estado"]["reiniciar"]
    assert "Resumir" not in " ".join(result["estado"]["reiniciar"])  # esse muda na hora
    with pytest.raises(PanelError):
        await akemi.apply_panel({"vida_enabled": False, "nvidia_api_key": "x"})
    await akemi.stop()
    assert akemi.settings.vida_enabled is True and akemi.settings.memory_keep_days == 3


@pytest.mark.asyncio
async def test_panel_end_to_end_over_http(tmp_path):
    akemi, tts = _akemi(tmp_path)
    await akemi.start()
    await _settle(akemi)
    assert akemi.start_panel() is None
    base, token = f"http://127.0.0.1:{akemi.panel.port}", akemi.panel.token
    status, body = await asyncio.to_thread(_request, base + "/api/estado", token=token)
    assert status == 200 and json.loads(body)["humor"]
    status, body = await asyncio.to_thread(
        _request, base + "/api/salvar", token=token, body={"mudancas": {"memory_consolidate_enabled": False}})
    await _settle(akemi)
    await akemi.stop()
    assert status == 200 and "Organizar a memória" in json.loads(body)["estado"]["desligadas"]
    assert akemi.state.mood == "triste" and akemi.panel is not None
