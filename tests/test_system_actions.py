"""O PC em si: configurações, info, energia, janelas, tema e volume exato (backend falso)."""

import asyncio
import ctypes
import sys

import pytest

from akemi.actions.desktop import PC_ACTIONS, StartApp, StartMenu, Window, register_desktop_actions
from akemi.actions.registry import ActionCall, ActionRegistry, Tier
from akemi.actions.system import (
    SYSTEM_ACTIONS,
    Battery,
    System,
    WindowsSystem,
    _guid,
    _MEMORYSTATUSEX,
    _MONITORINFO,
    _POWER_STATUS,
    find_settings_page,
    register_system_actions,
    say_size,
)
from akemi.core.events import Trust

GB = 2**30


class FakeSystem(System):
    def __init__(self):
        self.calls = []
        self.level, self.muted = 40, False
        self.bat: Battery | None = Battery(76, True, True)
        self.ip = "192.168.0.12"
        self.windows_had_shutdown = False

    def open_uri(self, uri):
        self.calls.append(("uri", uri))

    def battery(self):
        return self.bat

    def disk(self):
        return "C:", 120 * GB, 476 * GB

    def memory(self):
        return int(9.8 * GB), 16 * GB

    def local_ip(self):
        return self.ip

    def get_volume(self):
        return self.level, self.muted

    def set_volume(self, level):
        self.level, self.muted = level, False
        self.calls.append(("volume", level))

    def set_mute(self, mute):
        self.muted = mute
        self.calls.append(("mute", mute))

    def lock(self):
        self.calls.append(("lock",))

    def sleep(self):
        self.calls.append(("sleep",))

    def shutdown(self, restart):
        self.calls.append(("restart" if restart else "shutdown",))

    def cancel_shutdown(self):
        self.calls.append(("abort",))
        return self.windows_had_shutdown

    def minimize_all(self):
        self.calls.append(("minimize_all",))

    def restore_all(self):
        self.calls.append(("restore_all",))

    def place_window(self, hwnd, where):
        self.calls.append(("place", hwnd, where))

    def set_theme(self, dark):
        self.calls.append(("theme", dark))


WINDOWS = [Window(1, "#geral - Discord", "Discord"), Window(2, "Google Chrome", "chrome")]


def make(delay=0.01):
    reg, pc = ActionRegistry(), FakeSystem()
    by_name = {"discord": WINDOWS[0], "chrome": WINDOWS[1]}
    register_system_actions(reg, pc, lambda nome: by_name.get(nome.lower().strip()), power_delay_s=delay)
    return reg, pc


async def run(reg, name, trust=Trust.OWNER, **args):
    return await reg.request(ActionCall(name, args), trust)


# --------------------------------------------------------- configurações
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page,uri,said",
    [
        ("bluetooth", "ms-settings:bluetooth", "Abrindo as configurações de Bluetooth."),
        ("o som", "ms-settings:sound", "Abrindo as configurações de som."),
        ("Wi-Fi", "ms-settings:network-wifi", "Abrindo as configurações de Wi-Fi."),
        ("atualizações do windows", "ms-settings:windowsupdate", "Abrindo o Windows Update."),
        ("papel de parede", "ms-settings:personalization-background", "Abrindo o plano de fundo."),
        ("antivírus", "windowsdefender:", "Abrindo a Segurança do Windows."),
        ("", "ms-settings:", "Abrindo as Configurações."),
        ("aquela página lá", "ms-settings:", "Não sei qual página é essa, abri as Configurações."),
    ],
)
async def test_settings_pages(page, uri, said):
    reg, pc = make()
    r = await run(reg, "configuracoes", pagina=page)
    assert r.status == "executed" and r.value == said
    assert pc.calls == [("uri", uri)]


def test_settings_page_names():
    assert find_settings_page("configurações de energia")[0] == "ms-settings:powersleep"
    assert find_settings_page("modo avião")[0] == "ms-settings:network-airplanemode"
    assert find_settings_page("volume de cada app")[0] == "ms-settings:apps-volume"
    assert find_settings_page("algo que não existe") is None


# ------------------------------------------------------------------ info
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "what,said",
    [
        ("bateria", "A bateria tá em 76%, carregando."),
        ("disco", "No disco C: sobram 120 GB livres de 476 GB."),
        ("espaço", "No disco C: sobram 120 GB livres de 476 GB."),
        ("memória RAM", "A memória tá em 61% de uso: 9,8 GB de 16 GB."),
        ("ip", "O IP do PC na rede é 192.168.0.12."),
        ("volume", "O volume tá em 40%."),
    ],
)
async def test_pc_info(what, said):
    reg, _ = make()
    r = await run(reg, "info_pc", o_que=what)
    assert r.value == said
    assert reg.get("info_pc").speak_result


@pytest.mark.asyncio
async def test_pc_info_everything_and_desktop_without_battery():
    reg, pc = make()
    pc.bat, pc.ip, pc.muted = None, None, True
    assert (await run(reg, "info_pc", o_que="bateria")).value == "Esse PC não tem bateria, tá direto na tomada."
    assert (await run(reg, "info_pc", o_que="volume")).value == "O volume tá em 40%, no mudo."
    everything = (await run(reg, "info_pc")).value
    assert everything == ("No disco C: sobram 120 GB livres de 476 GB. A memória tá em 61% de uso: 9,8 GB de 16 GB. "
                          "Não achei o IP: o PC parece estar sem rede.")


def test_sizes_like_the_explorer():
    assert say_size(9.8 * GB) == "9,8 GB"
    assert say_size(476 * GB) == "476 GB"
    assert say_size(1.8 * 1024 * GB) == "1,8 TB"


# --------------------------------------------------------------- energia
@pytest.mark.asyncio
async def test_power_always_asks_first():
    reg, pc = make()
    assert reg.get("energia").tier == Tier.CONFIRM
    questions = {
        ("bloquear", 0): "Bloqueio o PC? Diz sim ou não.",
        ("suspender", 0): "Coloco o PC pra dormir? Diz sim ou não.",
        ("desligar", 0): "Desligo o PC agora? Diz sim ou não.",
        ("desligar", 60): "Desligo o PC daqui a 1 hora? Diz sim ou não.",
        ("reiniciar", 90): "Reinicio o PC daqui a 1 hora e 30 minutos? Diz sim ou não.",
    }
    for (acao, minutos), question in questions.items():
        r = await run(reg, "energia", acao=acao, minutos=minutos)
        assert r.status == "pending_confirmation" and r.detail == question
        assert (await reg.resolve_confirmation(False, Trust.OWNER)).status == "denied"
    assert (await run(reg, "energia", Trust.PUBLIC, acao="desligar")).status == "denied"  # chat, tela
    reg.allow_initiative(["energia", "cancelar_desligamento"])
    assert reg.initiative == {"cancelar_desligamento"}  # nada que pede "sim" vira iniciativa
    await asyncio.sleep(0.05)
    assert pc.calls == []


@pytest.mark.asyncio
async def test_lock_and_sleep_wait_for_her_to_answer():
    reg, pc = make(delay=0.05)
    await run(reg, "energia", acao="bloqueia")
    r = await reg.resolve_confirmation(True, Trust.OWNER)
    assert r.status == "executed" and r.value == "Bloqueando o PC."
    assert pc.calls == []  # ainda falando "feito"
    await asyncio.sleep(0.2)
    assert pc.calls == [("lock",)]
    await run(reg, "energia", acao="suspender")
    await reg.resolve_confirmation(True, Trust.OWNER)
    await asyncio.sleep(0.2)
    assert pc.calls[-1] == ("sleep",)


@pytest.mark.asyncio
async def test_shutdown_now_later_and_cancel():
    reg, pc = make()
    await run(reg, "energia", acao="desligar")
    await reg.resolve_confirmation(True, Trust.OWNER)
    await asyncio.sleep(0.1)
    assert pc.calls == [("shutdown",)]

    pc.calls.clear()
    await run(reg, "energia", acao="reiniciar", minutos="60 minutos")
    r = await reg.resolve_confirmation(True, Trust.OWNER)
    assert r.value == "Reinicio o PC daqui a 1 hora. Se mudar de ideia, fala 'cancela o desligamento'."
    await asyncio.sleep(0.05)
    assert pc.calls == []
    assert (await run(reg, "cancelar_desligamento")).value == "Cancelei, o PC fica ligado."
    assert (await run(reg, "cancelar_desligamento")).value == "Não tinha nenhum desligamento marcado."
    pc.windows_had_shutdown = True  # marcado por fora (shutdown /s /t)
    assert (await run(reg, "cancelar_desligamento")).value == "Cancelei, o PC fica ligado."
    assert ("restart",) not in pc.calls


@pytest.mark.asyncio
async def test_power_rejects_nonsense_before_asking():
    reg, pc = make()
    r = await run(reg, "energia", acao="explodir")
    assert r.status == "error" and reg.pending is None
    r = await run(reg, "energia", acao="desligar", minutos=60 * 48)
    assert r.status == "error" and "24 horas" in r.detail and reg.pending is None
    await asyncio.sleep(0.05)
    assert pc.calls == []


# --------------------------------------------------------------- janelas
@pytest.mark.asyncio
async def test_windows_and_desktop():
    reg, pc = make()
    assert (await run(reg, "janelas", comando="minimizar tudo")).value == "Pronto, área de trabalho limpa."
    assert (await run(reg, "janelas", comando="restaurar")).value == "Trouxe as janelas de volta."
    r = await run(reg, "posicionar_janela", nome="Discord", onde="na esquerda")
    assert r.value == "Discord na esquerda."
    await run(reg, "posicionar_janela", nome="chrome", onde="direita")
    await run(reg, "posicionar_janela", nome="chrome", onde="tela cheia")
    assert pc.calls == [("minimize_all",), ("restore_all",), ("place", 1, "esquerda"),
                        ("place", 2, "direita"), ("place", 2, "maximizar")]
    assert (await run(reg, "posicionar_janela", nome="photoshop", onde="esquerda")).status == "error"
    assert (await run(reg, "posicionar_janela", nome="discord", onde="no teto")).status == "error"


@pytest.mark.asyncio
async def test_theme_and_snip():
    reg, pc = make()
    assert (await run(reg, "tema", modo="escuro")).value == "Modo escuro ligado."
    assert (await run(reg, "tema", modo="claro")).value == "Modo claro ligado."
    assert (await run(reg, "tema", modo="roxo")).status == "error"
    await run(reg, "recortar_tela")
    assert pc.calls == [("theme", True), ("theme", False), ("uri", "ms-screenclip:")]


# ----------------------------------------------- junto com as outras do PC
def make_desktop():
    reg, pc, keys = ActionRegistry(), FakeSystem(), []
    menu = StartMenu(loader=lambda: [StartApp("Discord", "x")], launcher=lambda app: None)
    register_desktop_actions(reg, start_menu=menu, press=lambda vk, n: keys.append((vk, n)),
                             windows=lambda: WINDOWS, close=lambda w: None, open_url=lambda u: None, system=pc)
    return reg, pc, keys


@pytest.mark.asyncio
async def test_exact_volume_and_mute():
    reg, pc, keys = make_desktop()
    assert (await run(reg, "volume", nivel=30)).value == "Volume em 30%."
    assert (await run(reg, "volume", nivel="25%")).value == "Volume em 25%."
    assert (await run(reg, "volume", nivel=180)).value == "Volume em 100%."
    assert (await run(reg, "volume", comando="definir", quanto=15)).value == "Volume em 15%."
    assert (await run(reg, "volume", comando="mutar")).value == "Mudo."
    assert (await run(reg, "volume", comando="desmutar")).value == "Som de volta."
    assert pc.calls == [("volume", 30), ("volume", 25), ("volume", 100), ("volume", 15), ("mute", True), ("mute", False)]
    assert keys == []
    assert (await run(reg, "volume", nivel="alto")).status == "error"


@pytest.mark.asyncio
async def test_window_placement_finds_open_programs():
    reg, pc, _ = make_desktop()
    await run(reg, "posicionar_janela", nome="o discord", onde="esquerda")
    await run(reg, "posicionar_janela", nome="chrome", onde="direita")
    assert pc.calls == [("place", 1, "esquerda"), ("place", 2, "direita")]


def test_new_actions_go_to_the_body():
    assert set(SYSTEM_ACTIONS) <= set(PC_ACTIONS)
    reg, *_ = make_desktop()
    assert all(reg.get(name) is not None for name in SYSTEM_ACTIONS)


@pytest.mark.asyncio
async def test_without_windows_it_says_so():
    reg = ActionRegistry()
    register_system_actions(reg, System(), lambda nome: None)
    r = await run(reg, "tema", modo="escuro")
    assert r.status == "error" and r.detail == "só funciona no Windows"


# -------------------------------------------- o backend real do Windows
def test_windows_structures_have_the_api_sizes():
    # Tamanhos da documentação da API do Windows: um campo errado quebra a leitura.
    assert ctypes.sizeof(_POWER_STATUS) == 12
    assert ctypes.sizeof(_MEMORYSTATUSEX) == 64
    assert ctypes.sizeof(_MONITORINFO) == 40
    assert bytes(_guid("5CDF2C82-841E-4546-9722-0CF74078229A"))[:4] == bytes.fromhex("822cdf5c")


@pytest.mark.skipif(sys.platform != "win32", reason="só no Windows")
def test_windows_backend_reads_the_pc():
    pc = WindowsSystem()
    used, total = pc.memory()
    assert 0 < used < total
    drive, free, size = pc.disk()
    assert drive.endswith(":") and 0 < free < size
    bat = pc.battery()
    assert bat is None or 0 <= bat.percent <= 100
    try:
        level, muted = pc.get_volume()
    except OSError:
        pytest.skip("sem saída de áudio nesta máquina")
    assert 0 <= level <= 100 and isinstance(muted, bool)
