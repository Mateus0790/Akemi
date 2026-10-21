"""Ações no PC do dono (Windows): abrir e fechar programas, mídia, volume, pesquisa.

Todas exigem Trust.OWNER (o ``ActionRegistry`` garante isso): o chat da live,
a tela e o áudio do PC nunca disparam nada daqui.

Os "backends" (Menu Iniciar, teclas, janelas) são injetáveis, então a lógica é
testável sem Windows.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import subprocess
import sys
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse

import structlog

from akemi.safety.moderation import normalize

from .registry import ActionRegistry, Tier

logger = structlog.get_logger()

# Atalhos do Menu Iniciar que nunca são o que a pessoa quis abrir.
JUNK_APPS = re.compile(r"uninstall|desinstal|support|suporte|readme|leia-me|help|ajuda|manual|documenta|website|release notes", re.I)


def _norm(text: str) -> str:
    """Minúsculas, sem acento e sem pontuação: "Visual Studio Code" -> "visual studio code"."""
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", normalize(text)).split())


def _acronym_variants(name: str) -> set[str]:
    """Siglas faladas: "visual studio code" -> {"vsc", "vs code", "vscode"}."""
    words = name.split()
    out = set()
    for k in range(2, len(words) + 1):
        acr = "".join(w[0] for w in words[:k])
        rest = " ".join(words[k:])
        out |= {f"{acr} {rest}".strip(), f"{acr}{rest.replace(' ', '')}"}
    return out


# ------------------------------------------------------------- Menu Iniciar
@dataclass(frozen=True)
class StartApp:
    name: str
    app_id: str


def load_start_apps() -> list[StartApp]:
    """Programas do Menu Iniciar (inclui apps da Store e atalhos da Steam)."""
    if sys.platform != "win32":
        return []
    out = subprocess.run(  # noqa: S603
        ["powershell", "-NoProfile", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
        capture_output=True, timeout=30, check=False,
    )
    data = json.loads(out.stdout.decode("utf-8", "replace") or "[]")
    if isinstance(data, dict):
        data = [data]
    return [StartApp(str(a["Name"]), str(a["AppID"])) for a in data if a.get("Name") and a.get("AppID")]


def launch_app(app: StartApp) -> None:
    if re.match(r"^(steam|https?)://", app.app_id, re.I):
        os.startfile(app.app_id)  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app.app_id}"])  # noqa: S603, S607


class StartMenu:
    def __init__(
        self,
        loader: Callable[[], list[StartApp]] = load_start_apps,
        launcher: Callable[[StartApp], None] = launch_app,
    ) -> None:
        self.loader = loader
        self.launcher = launcher
        self._apps: list[StartApp] | None = None

    @property
    def apps(self) -> list[StartApp]:
        if self._apps is None:
            self._apps = self.loader()
        return self._apps

    def find(self, query: str) -> StartApp | None:
        """Acha o programa pelo nome falado ("discord", "o vs code", "calculadora")."""
        q = _norm(query)
        q = re.sub(r"^(o|a|os|as|meu|minha)\s+", "", q)
        if not q:
            return None
        scored: list[tuple[float, StartApp]] = []
        for app in self.apps:
            n = _norm(app.name)
            if not n:
                continue
            junk = bool(JUNK_APPS.search(app.name))
            if n == q:
                score = 3.0
            elif q in _acronym_variants(n):  # "vs code" -> "visual studio code"
                score = 2.5
            elif n.startswith(q) or set(q.split()) <= set(n.split()):
                score = 2.0
            elif q in n.replace(" ", "") or q.replace(" ", "") in n.replace(" ", ""):
                score = 1.5
            else:
                score = difflib.SequenceMatcher(None, q, n).ratio()
                if score < 0.75:
                    continue
            if junk and score < 3.0:
                score -= 1.0
            scored.append((score - len(n) / 1000, app))  # empate: nome mais curto
        if not scored:
            return None
        best = max(scored, key=lambda s: s[0])
        return best[1] if best[0] > 0.7 else None

    def open(self, query: str) -> StartApp:
        app = self.find(query)
        if app is None:
            self._apps = None  # pode ter sido instalado agora
            app = self.find(query)
        if app is None:
            raise ValueError(f"não achei nenhum programa chamado {query}")
        self.launcher(app)
        logger.info("app launched", app=app.name)
        return app


# --------------------------------------------------------- teclas de mídia
VK = {
    "proxima": 0xB0, "anterior": 0xB1, "parar": 0xB2, "tocar_pausar": 0xB3,
    "mutar": 0xAD, "volume_menos": 0xAE, "volume_mais": 0xAF,
}


def press_key(vk: int, times: int = 1) -> None:
    if sys.platform != "win32":
        raise RuntimeError("só funciona no Windows")
    import ctypes

    keybd_event = ctypes.windll.user32.keybd_event
    for _ in range(max(1, times)):
        keybd_event(vk, 0, 0, 0)
        keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


# ---------------------------------------------------------------- janelas
@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    process: str  # nome do executável, sem .exe


def list_windows() -> list[Window]:
    """Janelas visíveis com título (o que aparece na barra de tarefas)."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    windows: list[Window] = []

    def process_name(pid: int) -> str:
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.splitext(os.path.basename(buf.value))[0]
            return ""
        finally:
            kernel32.CloseHandle(handle)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # sem dono
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                windows.append(Window(int(hwnd), buf.value, process_name(pid.value)))
        return True

    user32.EnumWindows(callback, 0)
    return windows


def close_window(window: Window) -> None:
    """Pede para a janela fechar (como clicar no X): o programa pode perguntar
    se quer salvar. Nunca mata o processo à força."""
    import ctypes

    ctypes.windll.user32.PostMessageW(window.hwnd, 0x0010, 0, 0)  # WM_CLOSE


def find_windows(query: str, windows: list[Window]) -> list[Window]:
    q = _norm(query)
    q = re.sub(r"^(o|a|os|as|meu|minha)\s+", "", q)
    if not q:
        return []
    exact = [w for w in windows if _norm(w.process) == q.replace(" ", "")]
    if exact:
        return exact
    return [
        w for w in windows
        if q in _norm(w.title) or q.replace(" ", "") in _norm(w.process)
        or difflib.SequenceMatcher(None, q, _norm(w.process)).ratio() >= 0.8
    ]


# --------------------------------------------------------------- registro
# Ações que mexem no PC. No modo servidor + corpo, o núcleo encaminha só estas
# ao corpo, e o corpo só aceita estas.
PC_ACTIONS = ("abrir_programa", "abrir_site", "fechar_programa", "pesquisar", "midia", "volume")

def register_desktop_actions(
    registry: ActionRegistry,
    *,
    start_menu: StartMenu | None = None,
    press: Callable[[int, int], None] = press_key,
    windows: Callable[[], list[Window]] = list_windows,
    close: Callable[[Window], None] = close_window,
    open_url: Callable[[str], object] = webbrowser.open,
) -> None:
    menu = start_menu or StartMenu()

    @registry.register("abrir_programa", "abre qualquer programa ou jogo do Menu Iniciar pelo nome",
                       example=('abre o Spotify', '[[acao:abrir_programa {"nome": "Spotify"}]]'),
                       params={"nome": "nome do programa, ex. Discord, Spotify, VS Code"})
    async def abrir_programa(nome: str) -> str:
        # Get-StartApps leva ~1 s na primeira vez: fora do loop de eventos.
        app = await asyncio.to_thread(menu.open, nome)
        return f"Abrindo {app.name}."

    @registry.register("abrir_site", "abre um site no navegador", params={"url": "endereço http(s)"})
    def abrir_site(url: str) -> str:
        if "://" not in url:
            url = "https://" + url
        if urlparse(url).scheme not in ("http", "https"):
            raise ValueError("só http/https")
        open_url(url)
        return "Abrindo."

    @registry.register("fechar_programa", "fecha um programa aberto (como clicar no X)", tier=Tier.CONFIRM,
                       example=('fecha o Discord', '[[acao:fechar_programa {"nome": "Discord"}]]'),
                       params={"nome": "nome do programa"}, confirm="Fecho {nome}?")
    def fechar_programa(nome: str) -> str:
        found = find_windows(nome, windows())
        if not found:
            raise ValueError(f"não achei {nome} aberto")
        for w in found:
            close(w)
        return f"Fechei {nome}."

    @registry.register("pesquisar", "pesquisa no Google ou no YouTube e abre no navegador",
                       example=('pesquisa lo-fi no YouTube', '[[acao:pesquisar {"termo": "lo-fi", "onde": "youtube"}]]'),
                       params={"termo": "o que pesquisar", "onde": "google ou youtube (padrão google)"})
    def pesquisar(termo: str, onde: str = "google") -> str:
        termo = termo.strip()
        if not termo:
            raise ValueError("pesquisar o quê?")
        if "youtube" in onde.lower():
            open_url("https://www.youtube.com/results?search_query=" + quote_plus(termo))
        else:
            open_url("https://www.google.com/search?q=" + quote_plus(termo))
        return "Pesquisando."

    @registry.register("midia", "controla a música/vídeo tocando",
                       example=('pula essa música', '[[acao:midia {"comando": "proxima"}]]'),
                       params={"comando": "tocar, pausar, proxima ou anterior"})
    def midia(comando: str) -> str:
        c = normalize(comando)
        if c.startswith(("prox", "pul", "next", "avan")):
            press(VK["proxima"], 1)
        elif c.startswith(("ant", "volt", "prev")):
            press(VK["anterior"], 1)
        elif c.startswith(("toc", "paus", "play", "contin", "solt", "para")):
            press(VK["tocar_pausar"], 1)  # a mesma tecla alterna tocar/pausar
        else:
            raise ValueError(f"comando de mídia desconhecido: {comando}")
        return "ok"

    @registry.register("volume", "muda o volume do PC",
                       example=('abaixa o som', '[[acao:volume {"comando": "baixar", "quanto": 10}]]'),
                       params={"comando": "subir, baixar ou mutar", "quanto": "quanto em %, padrão 10"})
    def volume(comando: str, quanto: int | str = 10) -> str:
        c = normalize(comando)
        try:
            steps = max(1, min(50, round(int(str(quanto).strip(" %")) / 2)))  # cada tecla = 2%
        except ValueError:
            steps = 5
        if c.startswith(("sub", "aum", "mais", "alt")):
            press(VK["volume_mais"], steps)
        elif c.startswith(("baix", "dimi", "menos")):
            press(VK["volume_menos"], steps)
        elif c.startswith(("mut", "sil", "desmut", "tira o mudo")):
            press(VK["mutar"], 1)  # alterna
        else:
            raise ValueError(f"comando de volume desconhecido: {comando}")
        return "ok"
