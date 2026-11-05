"""Ações no PC com backends falsos (rodam em qualquer sistema)."""

import asyncio

import pytest

from akemi.actions.desktop import VK, StartApp, StartMenu, Window, find_windows, register_desktop_actions
from akemi.actions.registry import ActionCall, ActionRegistry
from akemi.actions.reminders import Reminders, register_reminder_actions
from akemi.core.events import Trust

# Amostra real do Menu Iniciar do dono (Get-StartApps).
APPS = [
    StartApp("Bloco de Notas", r"{1AC14E77}\notepad.exe"),
    StartApp("Calculadora", r"{1AC14E77}\win32calc.exe"),
    StartApp("Discord", "com.squirrel.Discord.Discord"),
    StartApp("Explorador de Arquivos", "Microsoft.Windows.Explorer"),
    StartApp("OBS Studio", "steam://rungameid/1905180"),
    StartApp("Spotify", r"C:\Users\Mateus\AppData\Roaming\Spotify\Spotify.exe"),
    StartApp("Steam", r"{7C5A40EF}\Steam\Steam.exe"),
    StartApp("Steam Support Center", "http://support.steampowered.com/"),
    StartApp("Visual Studio Code", "Microsoft.VisualStudioCode"),
    StartApp("Uninstall Discord", "x"),
]


def menu():
    launched = []
    return StartMenu(loader=lambda: APPS, launcher=launched.append), launched


@pytest.mark.parametrize(
    "query,expected",
    [("discord", "Discord"), ("o Discord", "Discord"), ("spotfy", "Spotify"), ("steam", "Steam"),
     ("vs code", "Visual Studio Code"), ("visual studio", "Visual Studio Code"), ("calculadora", "Calculadora"),
     ("bloco de notas", "Bloco de Notas"), ("obs", "OBS Studio"), ("explorador", "Explorador de Arquivos")],
)
def test_start_menu_finds_spoken_names(query, expected):
    m, _ = menu()
    assert m.find(query).name == expected


def test_start_menu_unknown_program():
    m, launched = menu()
    assert m.find("photoshop") is None
    with pytest.raises(ValueError, match="photoshop"):
        m.open("photoshop")
    assert launched == []


def make(windows=()):
    reg = ActionRegistry()
    m, launched = menu()
    keys, closed, urls = [], [], []
    register_desktop_actions(
        reg, start_menu=m, press=lambda vk, n: keys.append((vk, n)),
        windows=lambda: list(windows), close=closed.append, open_url=urls.append,
    )
    return reg, launched, keys, closed, urls


async def run(reg, name, trust=Trust.OWNER, **args):
    return await reg.request(ActionCall(name, args), trust)


@pytest.mark.asyncio
async def test_open_program_runs_directly_for_owner_only():
    reg, launched, *_ = make()
    r = await run(reg, "abrir_programa", nome="discord")
    assert r.status == "executed" and launched[0].name == "Discord"
    for trust in (Trust.PUBLIC, Trust.SYSTEM):  # chat, tela, pensamentos
        assert (await run(reg, "abrir_programa", trust, nome="spotify")).status == "denied"
    assert len(launched) == 1


@pytest.mark.asyncio
async def test_media_and_volume_keys():
    reg, _, keys, *_ = make()
    for cmd in ("pausar", "tocar", "próxima", "anterior"):
        assert (await run(reg, "midia", comando=cmd)).status == "executed"
    await run(reg, "volume", comando="subir", quanto="20%")
    await run(reg, "volume", comando="baixar")
    await run(reg, "volume", comando="mutar")
    assert keys == [
        (VK["tocar_pausar"], 1), (VK["tocar_pausar"], 1), (VK["proxima"], 1), (VK["anterior"], 1),
        (VK["volume_mais"], 10), (VK["volume_menos"], 5), (VK["mutar"], 1),
    ]
    assert (await run(reg, "midia", comando="dançar")).status == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["WhatsApp Web", "o whatsapp web", "web.whatsapp.com", "https://web.whatsapp.com"])
async def test_open_site_by_name_or_address(url):
    reg, *_, urls = make()
    r = await run(reg, "abrir_site", url=url)
    assert r.status == "executed"
    assert urls == ["https://web.whatsapp.com"]


@pytest.mark.asyncio
async def test_open_site_rejects_unknown_name_and_bad_scheme():
    reg, *_, urls = make()
    for url in ("site inventado", "file:///c:/x"):
        r = await run(reg, "abrir_site", url=url)
        assert r.status == "error"
    assert urls == []


@pytest.mark.asyncio
async def test_open_program_falls_back_to_known_site():
    reg, launched, *_, urls = make()
    r = await run(reg, "abrir_programa", nome="WhatsApp Web")
    assert r.status == "executed"
    assert urls == ["https://web.whatsapp.com"]
    assert launched == []


@pytest.mark.asyncio
async def test_web_name_opens_site_even_with_app_installed():
    apps = APPS + [StartApp("WhatsApp", "5319275A.WhatsAppDesktop")]
    launched, urls = [], []
    reg = ActionRegistry()
    register_desktop_actions(reg, start_menu=StartMenu(loader=lambda: apps, launcher=launched.append), open_url=urls.append)
    await run(reg, "abrir_programa", nome="WhatsApp Web")
    assert urls == ["https://web.whatsapp.com"] and launched == []
    await run(reg, "abrir_programa", nome="WhatsApp")
    assert [a.name for a in launched] == ["WhatsApp"] and len(urls) == 1


@pytest.mark.asyncio
async def test_unknown_program_still_fails():
    reg, *_, urls = make()
    r = await run(reg, "abrir_programa", nome="photoshop")
    assert r.status == "error"
    assert urls == []


@pytest.mark.asyncio
async def test_search():
    reg, *_, urls = make()
    await run(reg, "pesquisar", termo="receita de bolo", onde="google")
    await run(reg, "pesquisar", termo="lo-fi beats", onde="YouTube")
    assert urls == [
        "https://www.google.com/search?q=receita+de+bolo",
        "https://www.youtube.com/results?search_query=lo-fi+beats",
    ]


WINDOWS = [
    Window(1, "#geral | Servidor - Discord", "Discord"),
    Window(2, "Spotify Premium", "Spotify"),
    Window(3, "main.py - Akemi - Visual Studio Code", "Code"),
]


def test_find_windows():
    assert [w.hwnd for w in find_windows("discord", WINDOWS)] == [1]
    assert [w.hwnd for w in find_windows("o spotify", WINDOWS)] == [2]
    assert [w.hwnd for w in find_windows("visual studio code", WINDOWS)] == [3]
    assert find_windows("photoshop", WINDOWS) == []


@pytest.mark.asyncio
async def test_close_program_asks_first():
    reg, _, _, closed, _ = make(WINDOWS)
    r = await run(reg, "fechar_programa", nome="Discord")
    assert r.status == "pending_confirmation" and r.detail == "Fecho Discord? Diz sim ou não."
    assert closed == []
    r = await reg.resolve_confirmation(True, Trust.OWNER)
    assert r.status == "executed" and [w.hwnd for w in closed] == [1]


@pytest.mark.asyncio
async def test_close_program_not_open():
    reg, *_ = make(WINDOWS)
    await run(reg, "fechar_programa", nome="photoshop")
    r = await reg.resolve_confirmation(True, Trust.OWNER)
    assert r.status == "error" and "photoshop" in r.detail


@pytest.mark.asyncio
async def test_reminders_fire_and_cancel():
    fired = []
    reg = ActionRegistry()
    reminders = Reminders(fired.append)
    register_reminder_actions(reg, reminders)
    r = await reg.request(ActionCall("lembrete", {"minutos": "0,002", "texto": "beber água"}), Trust.OWNER)
    assert r.status == "executed"
    await reg.request(ActionCall("lembrete", {"minutos": 30, "texto": "reunião"}), Trust.OWNER)
    await asyncio.sleep(0.3)
    assert fired == ["beber água"] and [x.text for x in reminders.pending] == ["reunião"]
    r = await reg.request(ActionCall("cancelar_lembretes", {}), Trust.OWNER)
    assert r.value == "Cancelei 1 lembrete(s)." and reminders.pending == []
    bad = await reg.request(ActionCall("lembrete", {"minutos": "amanhã", "texto": "x"}), Trust.OWNER)
    assert bad.status == "error"


@pytest.mark.asyncio
async def test_reminder_is_spoken(tmp_path):
    from akemi.brain.providers import EchoProvider
    from akemi.core.config import Settings
    from akemi.core.orchestrator import Akemi
    from akemi.tts.base import TTSEngine

    class Rec(TTSEngine):
        said = []

        async def speak(self, text):
            self.said.append(text)

    settings = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0)
    tts, llm = Rec(), EchoProvider(lambda m, j: '[[acao:lembrete {"minutos": 0.002, "texto": "beber água"}]]')
    akemi = Akemi(settings, fast=llm, deep=llm, tts=tts, persist=False, desktop_actions=False)
    assert akemi.actions.get("abrir_programa") is None  # sem ações de PC quando desligadas
    await akemi.start()
    akemi.owner_say("me lembra de beber água")
    await akemi.bus.drain()
    await asyncio.sleep(0.5)
    await akemi.stop()
    assert tts.said == ["Combinado, te lembro em 0.002 minutos.", "Ei, Mateus, lembrete: beber água"]
