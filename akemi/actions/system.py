"""O PC em si (Windows): páginas das Configurações, bateria/disco/memória/IP,
volume exato, energia, janelas, tema escuro/claro e recorte de tela.

O backend do Windows (``WindowsSystem``) usa só a biblioteca padrão (ctypes,
winreg) e programas do próprio Windows: nada para instalar. Ele é injetável:
os testes usam um falso, como nas outras ações do PC.

Desligar, reiniciar, suspender e bloquear pedem "sim" (``Tier.CONFIRM``). O
desligamento marcado ("daqui a uma hora") fica num relógio da Akemi e roda
``shutdown`` sem forçar: o Windows ainda pergunta se quer salvar o que estiver aberto.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import re
import shutil
import socket
import subprocess
import sys
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from .registry import ActionRegistry, Tier

logger = structlog.get_logger()

# Ações daqui que o núcleo encaminha ao corpo (entram em ``PC_ACTIONS``).
SYSTEM_ACTIONS = (
    "configuracoes", "info_pc", "energia", "cancelar_desligamento",
    "janelas", "posicionar_janela", "tema", "recortar_tela",
)
MAX_SHUTDOWN_MIN = 24 * 60


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower().strip()


@dataclass(frozen=True)
class Battery:
    percent: int
    plugged: bool
    charging: bool


class System:
    """O que funciona em qualquer sistema; o resto só no Windows."""

    def _windows_only(self) -> Any:
        raise RuntimeError("só funciona no Windows")

    def open_uri(self, uri: str) -> None:
        self._windows_only()

    def battery(self) -> Battery | None:
        return self._windows_only()

    def disk(self) -> tuple[str, int, int]:
        """(unidade, livre, total) em bytes, do disco do sistema."""
        anchor = Path.home().anchor or "/"
        usage = shutil.disk_usage(anchor)
        return anchor.rstrip("\\/") or "/", usage.free, usage.total

    def memory(self) -> tuple[int, int]:
        """(em uso, total) em bytes."""
        return self._windows_only()

    def local_ip(self) -> str | None:
        """O IP do PC na rede de casa (não o da internet: isso seria perguntar a um site)."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))  # UDP: não manda nada, só escolhe a interface
                ip = s.getsockname()[0]
        except OSError:
            return None
        return None if ip.startswith(("127.", "0.", "169.254.")) else ip

    def get_volume(self) -> tuple[int, bool]:
        """(nível 0-100, no mudo?) da saída de áudio padrão."""
        return self._windows_only()

    def set_volume(self, level: int) -> None:
        self._windows_only()

    def set_mute(self, mute: bool) -> None:
        self._windows_only()

    def lock(self) -> None:
        self._windows_only()

    def sleep(self) -> None:
        self._windows_only()

    def shutdown(self, restart: bool) -> None:
        self._windows_only()

    def cancel_shutdown(self) -> bool:
        """Cancela um desligamento marcado no próprio Windows. True se havia um."""
        return self._windows_only()

    def minimize_all(self) -> None:
        self._windows_only()

    def restore_all(self) -> None:
        self._windows_only()

    def place_window(self, hwnd: int, where: str) -> None:
        """``where``: esquerda, direita, maximizar, minimizar ou restaurar."""
        self._windows_only()

    def set_theme(self, dark: bool) -> None:
        self._windows_only()


# ------------------------------------------------------------------ Windows
def _fn(dll: Any, name: str, restype: Any, *argtypes: Any) -> Any:
    """Função da API do Windows com tipos próprios (sem mexer nos de quem mais usa a DLL)."""
    return ctypes.WINFUNCTYPE(restype, *argtypes)((name, dll))


_GUID = ctypes.c_byte * 16


def _guid(text: str) -> Any:
    return _GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int32), ("top", ctypes.c_int32), ("right", ctypes.c_int32), ("bottom", ctypes.c_int32)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", ctypes.c_uint32)]


class _POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", ctypes.c_uint32), ("BatteryFullLifeTime", ctypes.c_uint32),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


class _EndpointVolume:
    """O volume da saída padrão pela Core Audio do Windows (IAudioEndpointVolume),
    chamando a COM direto pelo ctypes. Use com ``with``, numa thread só."""

    CLSID_ENUMERATOR = "BCDE0395-E52F-467C-8E3D-C4579291692E"
    IID_ENUMERATOR = "A95664D2-9614-4F35-A746-DE8DB63617E6"
    IID_VOLUME = "5CDF2C82-841E-4546-9722-0CF74078229A"
    CLSCTX_ALL = 0x17

    def __enter__(self) -> _EndpointVolume:
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
        hr = ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        self._uninit = hr in (0, 1)  # S_OK, S_FALSE: tem que fechar depois
        self._held: list[ctypes.c_void_p] = []
        try:
            enum = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(ctypes.byref(_guid(self.CLSID_ENUMERATOR)), None, self.CLSCTX_ALL,
                                        ctypes.byref(_guid(self.IID_ENUMERATOR)), ctypes.byref(enum))
            if hr < 0 or not enum:
                raise OSError(f"Core Audio indisponível (0x{hr & 0xFFFFFFFF:08X})")
            self._held.append(enum)
            device = ctypes.c_void_p()
            # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender, eMultimedia, &device)
            self._call(enum, 4, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(0, 1, ctypes.byref(device))
            self._held.append(device)
            volume = ctypes.c_void_p()
            # IMMDevice::Activate(IID_IAudioEndpointVolume, CLSCTX_ALL, NULL, &volume)
            self._call(device, 3, ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p,
                       ctypes.POINTER(ctypes.c_void_p))(ctypes.byref(_guid(self.IID_VOLUME)), self.CLSCTX_ALL,
                                                        None, ctypes.byref(volume))
            self._held.append(volume)
            self.volume = volume
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
        for obj in reversed(self._held):
            if obj:
                release(self._vtable(obj)[2])(obj)  # IUnknown::Release
        self._held.clear()
        if self._uninit:
            ctypes.windll.ole32.CoUninitialize()  # type: ignore[attr-defined]

    @staticmethod
    def _vtable(obj: ctypes.c_void_p) -> Any:
        return ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]

    def _call(self, obj: ctypes.c_void_p, index: int, *argtypes: Any) -> Callable[..., Any]:
        method = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(self._vtable(obj)[index])  # type: ignore[attr-defined]
        return lambda *args: method(obj, *args)

    # IAudioEndpointVolume: 7 SetMasterVolumeLevelScalar, 9 GetMasterVolumeLevelScalar, 14 SetMute, 15 GetMute
    @property
    def level(self) -> int:
        value = ctypes.c_float()
        self._call(self.volume, 9, ctypes.POINTER(ctypes.c_float))(ctypes.byref(value))
        return round(value.value * 100)

    @level.setter
    def level(self, level: int) -> None:
        self._call(self.volume, 7, ctypes.c_float, ctypes.c_void_p)(max(0, min(100, level)) / 100, None)

    @property
    def muted(self) -> bool:
        value = ctypes.c_int()
        self._call(self.volume, 15, ctypes.POINTER(ctypes.c_int))(ctypes.byref(value))
        return bool(value.value)

    @muted.setter
    def muted(self, mute: bool) -> None:
        self._call(self.volume, 14, ctypes.c_int, ctypes.c_void_p)(int(mute), None)


def _powershell(command: str, timeout: float = 20) -> str:
    out = subprocess.run(  # noqa: S603
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],  # noqa: S607
        capture_output=True, timeout=timeout, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()[:200] or "o PowerShell falhou")
    return out.stdout.decode("utf-8", "replace")


class WindowsSystem(System):
    def open_uri(self, uri: str) -> None:
        os.startfile(uri)  # type: ignore[attr-defined]  # noqa: S606

    def battery(self) -> Battery | None:
        st = _POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(st)):  # type: ignore[attr-defined]
            raise OSError("não consegui ler a bateria")
        if st.BatteryFlag & 128 or st.BatteryLifePercent > 100:  # 128: sem bateria; 255: desconhecido
            return None
        return Battery(int(st.BatteryLifePercent), st.ACLineStatus == 1, bool(st.BatteryFlag & 8))

    def disk(self) -> tuple[str, int, int]:
        drive = os.environ.get("SystemDrive", "C:")
        usage = shutil.disk_usage(drive + "\\")
        return drive, usage.free, usage.total

    def memory(self) -> tuple[int, int]:
        m = _MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(m)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):  # type: ignore[attr-defined]
            raise OSError("não consegui ler a memória")
        return m.ullTotalPhys - m.ullAvailPhys, m.ullTotalPhys

    def get_volume(self) -> tuple[int, bool]:
        with _EndpointVolume() as v:
            return v.level, v.muted

    def set_volume(self, level: int) -> None:
        with _EndpointVolume() as v:
            v.level = level
            v.muted = False

    def set_mute(self, mute: bool) -> None:
        with _EndpointVolume() as v:
            v.muted = mute

    def lock(self) -> None:
        if not ctypes.windll.user32.LockWorkStation():  # type: ignore[attr-defined]
            raise OSError("o Windows não deixou bloquear")

    def sleep(self) -> None:
        # SetSuspendState(hibernar=False, forçar=False, sem acordar por timer=False)
        if not ctypes.windll.powrprof.SetSuspendState(0, 0, 0):  # type: ignore[attr-defined]
            raise OSError("o Windows não deixou suspender")

    def shutdown(self, restart: bool) -> None:
        # Sem /t: o "/f" (fechar tudo sem salvar) só vem junto com /t maior que 0.
        result = subprocess.run(["shutdown", "/r" if restart else "/s", "/t", "0"],  # noqa: S603, S607
                                capture_output=True, check=False)
        if result.returncode != 0:
            raise OSError(result.stderr.decode("utf-8", "replace").strip()[:200] or "o Windows recusou")

    def cancel_shutdown(self) -> bool:
        # 1116: não havia desligamento marcado
        return subprocess.run(["shutdown", "/a"], capture_output=True, check=False).returncode == 0  # noqa: S603, S607

    def minimize_all(self) -> None:
        _powershell("(New-Object -ComObject Shell.Application).MinimizeAll()")

    def restore_all(self) -> None:
        _powershell("(New-Object -ComObject Shell.Application).UndoMinimizeALL()")

    def place_window(self, hwnd: int, where: str) -> None:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        HWND = ctypes.c_void_p
        show = _fn(user32, "ShowWindow", ctypes.c_int, HWND, ctypes.c_int)
        h = HWND(hwnd)
        if where in ("minimizar", "maximizar", "restaurar"):
            show(h, {"minimizar": 6, "maximizar": 3, "restaurar": 9}[where])
            return
        try:  # coordenadas reais da tela, mesmo com escala de 125%, 150%...
            set_dpi = _fn(user32, "SetThreadDpiAwarenessContext", ctypes.c_void_p, ctypes.c_void_p)
            previous = set_dpi(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        except AttributeError:
            set_dpi, previous = None, None
        try:
            show(h, 9)  # SW_RESTORE: maximizada não se move
            monitor = _fn(user32, "MonitorFromWindow", ctypes.c_void_p, HWND, ctypes.c_uint32)(h, 2)
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            _fn(user32, "GetMonitorInfoW", ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_MONITORINFO))(
                monitor, ctypes.byref(info))
            work = info.rcWork
            outer, frame = _RECT(), _RECT()
            _fn(user32, "GetWindowRect", ctypes.c_int, HWND, ctypes.POINTER(_RECT))(h, ctypes.byref(outer))
            dwm = _fn(ctypes.windll.dwmapi, "DwmGetWindowAttribute", ctypes.c_long,  # type: ignore[attr-defined]
                      HWND, ctypes.c_uint32, ctypes.POINTER(_RECT), ctypes.c_uint32)
            if dwm(h, 9, ctypes.byref(frame), ctypes.sizeof(frame)) != 0:  # DWMWA_EXTENDED_FRAME_BOUNDS
                frame = outer
            # A janela tem bordas invisíveis: a parte visível é que encosta na metade da tela.
            bl, bt = frame.left - outer.left, frame.top - outer.top
            br, bb = outer.right - frame.right, outer.bottom - frame.bottom
            width = work.right - work.left
            half = width // 2
            x, w = (work.left, half) if where == "esquerda" else (work.left + half, width - half)
            set_pos = _fn(user32, "SetWindowPos", ctypes.c_int, HWND, HWND, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, ctypes.c_uint32)
            if not set_pos(h, None, x - bl, work.top - bt, w + bl + br, work.bottom - work.top + bt + bb, 0x0040):
                raise OSError("o Windows não deixou mover a janela")
            _fn(user32, "SetForegroundWindow", ctypes.c_int, HWND)(h)
        finally:
            if set_dpi is not None and previous:
                set_dpi(previous)

    def set_theme(self, dark: bool) -> None:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0 if dark else 1)
        # Avisa as janelas abertas para mudarem na hora (WM_SETTINGCHANGE "ImmersiveColorSet").
        send = _fn(ctypes.windll.user32, "SendMessageTimeoutW", ctypes.c_ssize_t, ctypes.c_void_p,  # type: ignore[attr-defined]
                   ctypes.c_uint32, ctypes.c_size_t, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                   ctypes.POINTER(ctypes.c_size_t))
        send(ctypes.c_void_p(0xFFFF), 0x001A, 0, "ImmersiveColorSet", 0x0002, 3000, ctypes.byref(ctypes.c_size_t()))


def default_system() -> System:
    return WindowsSystem() if sys.platform == "win32" else System()


# ------------------------------------------------- páginas das Configurações
# (padrão, endereço, como ela fala). Os mais específicos primeiro.
SETTINGS_PAGES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(p), uri, label) for p, uri, label in [
        (r"bluetooth", "ms-settings:bluetooth", "as configurações de Bluetooth"),
        (r"mixer|volume (?:dos|de cada|por) (?:app|programa)", "ms-settings:apps-volume", "o mixer de volume"),
        (r"\bsom\b|audio|alto.?falante|\bfones?\b|microfone|saida de som", "ms-settings:sound", "as configurações de som"),
        (r"modo aviao|\baviao\b", "ms-settings:network-airplanemode", "o modo avião"),
        (r"wi.?fi|wireless", "ms-settings:network-wifi", "as configurações de Wi-Fi"),
        (r"\bvpn\b", "ms-settings:network-vpn", "as configurações de VPN"),
        (r"\brede\b|internet|ethernet|\bcabo\b", "ms-settings:network-status", "as configurações de rede"),
        (r"update|atualiza", "ms-settings:windowsupdate", "o Windows Update"),
        (r"\btela\b|video|monitor|resolucao|brilho|display|escala", "ms-settings:display", "as configurações de tela"),
        (r"notifica|nao perturbe", "ms-settings:notifications", "as notificações"),
        (r"mouse|touchpad|cursor", "ms-settings:mousetouchpad", "as configurações do mouse"),
        (r"economia de (?:energia|bateria)|bateria", "ms-settings:batterysaver", "as configurações de bateria"),
        (r"energia|suspen|desligar a tela", "ms-settings:powersleep", "as configurações de energia"),
        (r"plano de fundo|papel de parede|wallpaper", "ms-settings:personalization-background", "o plano de fundo"),
        (r"\bcor(?:es)?\b|modo escuro|modo claro", "ms-settings:personalization-colors", "as cores"),
        (r"\btemas?\b|personaliza", "ms-settings:themes", "os temas"),
        (r"inicializa|iniciar com o windows|startup", "ms-settings:startupapps", "os apps da inicialização"),
        (r"armazenamento|\bdisco\b|espaco|limpeza", "ms-settings:storagesense", "o armazenamento"),
        (r"aplicativos|\bapps?\b|programas|desinstala", "ms-settings:appsfeatures", "os aplicativos instalados"),
        (r"impressora|scanner", "ms-settings:printers", "as impressoras"),
        (r"teclado|digita", "ms-settings:typing", "as configurações de digitação"),
        (r"idioma|regiao|lingua", "ms-settings:regionlanguage", "idioma e região"),
        (r"\bdata\b|\bhora\b|relogio|fuso", "ms-settings:dateandtime", "data e hora"),
        (r"camera|webcam", "ms-settings:privacy-webcam", "as permissões da câmera"),
        (r"privacidade|permiss", "ms-settings:privacy", "a privacidade"),
        (r"seguranca|antivirus|defender|virus|firewall", "windowsdefender:", "a Segurança do Windows"),
        (r"senha|\bpin\b|entrar|login|windows hello", "ms-settings:signinoptions", "as opções de entrada"),
        (r"\bconta\b|perfil", "ms-settings:yourinfo", "a sua conta"),
        (r"area de transferencia|clipboard", "ms-settings:clipboard", "a área de transferência"),
        (r"game ?bar|modo de jogo|\bjogos\b", "ms-settings:gaming-gamebar", "as configurações de jogos"),
        (r"acessibilidade|lupa|narrador", "ms-settings:easeofaccess", "a acessibilidade"),
        (r"\bsobre\b|especifica|nome do pc|versao do windows", "ms-settings:about", "as informações do PC"),
    ]
]


def find_settings_page(query: str) -> tuple[str, str] | None:
    q = fold(query)
    for pattern, uri, label in SETTINGS_PAGES:
        if pattern.search(q):
            return uri, label
    return None


# --------------------------------------------------------------- como falar
def say_size(n: float) -> str:
    """Bytes como o Explorer mostra: "9,8 GB", "476 GB", "1,8 TB"."""
    gb = n / 2**30
    if gb >= 1000:
        return f"{gb / 1024:.1f} TB".replace(".", ",")
    if gb >= 10:
        return f"{gb:.0f} GB"
    return f"{gb:.1f} GB".replace(".", ",")


def say_minutes(minutes: int) -> str:
    if minutes <= 0:
        return "agora"
    h, m = divmod(minutes, 60)
    parts = []
    if h:
        parts.append(f"{h} hora{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minuto{'s' if m != 1 else ''}")
    return "daqui a " + " e ".join(parts)


_POWER = (
    ("bloquear", ("bloq", "trav", "tranc", "lock")),
    ("suspender", ("susp", "dorm", "hiber", "repous", "sleep")),
    ("reiniciar", ("reinic", "rein", "reset", "restart")),
    ("desligar", ("deslig", "deslg", "deslic", "shut", "deslija")),
)


def power_kind(acao: str) -> str | None:
    a = fold(acao)
    for kind, starts in _POWER:
        if a.startswith(starts):
            return kind
    return None


def _minutes(value: Any) -> int:
    text = str(value if value is not None else "").strip()
    if not text:
        return 0
    m = re.search(r"\d+(?:[.,]\d+)?", text)
    if m is None:
        raise ValueError(f"não entendi em quantos minutos: {value}")
    minutes = round(float(m[0].replace(",", ".")))
    if not 0 <= minutes <= MAX_SHUTDOWN_MIN:
        raise ValueError("só marco o desligamento até 24 horas pra frente")
    return minutes


def power_question(acao: str = "", minutos: Any = 0) -> str:
    """A pergunta antes do "sim". Pedido sem sentido nem chega a perguntar."""
    kind = power_kind(str(acao))
    if kind is None:
        raise ValueError(f"não sei fazer '{acao}' com o PC")
    when = say_minutes(_minutes(minutos))
    return {
        "bloquear": "Bloqueio o PC?",
        "suspender": "Coloco o PC pra dormir?",
        "desligar": f"Desligo o PC {when}?",
        "reiniciar": f"Reinicio o PC {when}?",
    }[kind]


# ------------------------------------------------------------------ registro
def register_system_actions(
    registry: ActionRegistry,
    system: System,
    find_window: Callable[[str], Any | None],
    *,
    power_delay_s: float = 3.0,
) -> None:
    """``find_window(nome)`` devolve a janela (com ``hwnd``) ou None.

    ``power_delay_s``: espera antes de bloquear/suspender/desligar, para ela
    terminar de falar "feito".
    """
    pending: dict[str, asyncio.Task] = {}  # o desligamento marcado, se houver
    running: set[asyncio.Task] = set()  # sem referência, o asyncio pode descartar a tarefa

    def start(coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        running.add(task)
        task.add_done_callback(running.discard)
        return task

    async def later(delay: float, fn: Callable[[], None], what: str) -> None:
        await asyncio.sleep(delay)
        try:
            await asyncio.to_thread(fn)
            logger.info("power action done", action=what)
        except Exception:
            logger.exception("power action failed", action=what)

    @registry.register(
        "configuracoes", "abre uma página das Configurações do Windows",
        example=("abre as configurações de bluetooth", '[[acao:configuracoes {"pagina": "bluetooth"}]]'),
        params={"pagina": "qual: bluetooth, som, wifi, rede, windows update, tela, notificações, mouse, "
                          "bateria, energia, plano de fundo, armazenamento, aplicativos, impressoras..."},
    )
    def configuracoes(pagina: str = "") -> str:
        found = find_settings_page(pagina) if pagina.strip() else None
        if found is None:
            system.open_uri("ms-settings:")
            return "Não sei qual página é essa, abri as Configurações." if pagina.strip() else "Abrindo as Configurações."
        uri, label = found
        system.open_uri(uri)
        return f"Abrindo {label}."

    @registry.register(
        "info_pc", "diz como está o PC: bateria, espaço no disco, memória RAM, IP na rede ou volume",
        speak_result=True,
        example=("quanto de bateria tem?", '[[acao:info_pc {"o_que": "bateria"}]]'),
        params={"o_que": "bateria, disco, memoria, ip, volume ou tudo"},
    )
    async def info_pc(o_que: str = "tudo") -> str:
        q = fold(o_que)
        if q.startswith(("bat", "carga")):
            return await asyncio.to_thread(_say_battery, system)
        if q.startswith(("disc", "armaz", "espa", "hd", "ssd")):
            return await asyncio.to_thread(_say_disk, system)
        if q.startswith(("mem", "ram")):
            return await asyncio.to_thread(_say_memory, system)
        if q.startswith(("ip", "rede", "ender", "internet")):
            return _say_ip(system)
        if q.startswith(("vol", "som", "audio")):
            level, muted = await asyncio.to_thread(system.get_volume)
            return f"O volume tá em {level}%{', no mudo' if muted else ''}."
        parts = []
        for say in (_say_battery, _say_disk, _say_memory):
            try:
                text = await asyncio.to_thread(say, system)
            except Exception as exc:
                logger.warning("pc info failed", part=say.__name__, error=str(exc))
                continue
            if not (say is _say_battery and text.startswith("Esse PC")):
                parts.append(text)
        parts.append(_say_ip(system))
        return " ".join(parts)

    @registry.register(
        "energia", "bloqueia, suspende, desliga ou reinicia o PC", tier=Tier.CONFIRM, confirm=power_question,
        example=("desliga o PC daqui a uma hora", '[[acao:energia {"acao": "desligar", "minutos": 60}]]'),
        params={"acao": "bloquear, suspender, desligar ou reiniciar",
                "minutos": "só para desligar/reiniciar: daqui a quantos minutos (padrão: agora)"},
    )
    async def energia(acao: str, minutos: int | str = 0) -> str:
        kind = power_kind(acao)
        if kind is None:
            raise ValueError(f"não sei fazer '{acao}' com o PC")
        if kind in ("bloquear", "suspender"):
            start(later(power_delay_s, system.lock if kind == "bloquear" else system.sleep, kind))
            return "Bloqueando o PC." if kind == "bloquear" else "Colocando o PC pra dormir."
        minutes = _minutes(minutos)
        if old := pending.pop("shutdown", None):
            old.cancel()
        restart = kind == "reiniciar"
        pending["shutdown"] = start(later(max(power_delay_s, minutes * 60), lambda: system.shutdown(restart), kind))
        verb = "Reinicio" if restart else "Desligo"
        if minutes == 0:
            return f"{'Reiniciando' if restart else 'Desligando'} o PC."
        return f"{verb} o PC {say_minutes(minutes)}. Se mudar de ideia, fala 'cancela o desligamento'."

    @registry.register(
        "cancelar_desligamento", "cancela o desligamento ou reinício marcado",
        example=("cancela o desligamento", "[[acao:cancelar_desligamento]]"),
    )
    async def cancelar_desligamento() -> str:
        task = pending.pop("shutdown", None)
        ours = task is not None and not task.done()
        if task is not None:
            task.cancel()
        try:
            windows = await asyncio.to_thread(system.cancel_shutdown)
        except RuntimeError:
            windows = False
        if ours or windows:
            return "Cancelei, o PC fica ligado."
        return "Não tinha nenhum desligamento marcado."

    @registry.register(
        "janelas", "minimiza todas as janelas (mostra a área de trabalho) ou traz todas de volta",
        example=("minimiza tudo", '[[acao:janelas {"comando": "minimizar"}]]'),
        params={"comando": "minimizar ou restaurar"},
    )
    async def janelas(comando: str = "minimizar") -> str:
        if fold(comando).startswith(("rest", "volt", "traz", "abr", "desfaz", "mostra as", "desminim")):
            await asyncio.to_thread(system.restore_all)
            return "Trouxe as janelas de volta."
        await asyncio.to_thread(system.minimize_all)  # minimizar, esconder, mostrar a área de trabalho
        return "Pronto, área de trabalho limpa."

    @registry.register(
        "posicionar_janela", "coloca a janela de um programa na metade esquerda ou direita da tela, "
        "ou maximiza, minimiza ou restaura",
        example=("põe o Discord na esquerda", '[[acao:posicionar_janela {"nome": "Discord", "onde": "esquerda"}]]'),
        params={"nome": "nome do programa", "onde": "esquerda, direita, maximizar, minimizar ou restaurar"},
    )
    async def posicionar_janela(nome: str, onde: str) -> str:
        o = re.sub(r"^(?:na|no|pra|para|em|a|o)\s+", "", fold(onde))
        where = next((w for w, starts in _PLACES if o.startswith(starts)), None)
        if where is None:
            raise ValueError(f"não sei pôr a janela em '{onde}'")
        window = find_window(nome)
        if window is None:
            raise ValueError(f"não achei {nome} aberto")
        await asyncio.to_thread(system.place_window, window.hwnd, where)
        done = {"esquerda": "na esquerda", "direita": "na direita", "maximizar": "maximizado",
                "minimizar": "minimizado", "restaurar": "restaurado"}[where]
        return f"{nome.strip()} {done}."

    @registry.register(
        "tema", "deixa o Windows no modo escuro ou claro",
        example=("coloca o modo escuro", '[[acao:tema {"modo": "escuro"}]]'),
        params={"modo": "escuro ou claro"},
    )
    async def tema(modo: str) -> str:
        m = fold(modo)
        if m.startswith(("esc", "dark", "noturn", "pret")):
            dark = True
        elif m.startswith(("clar", "light", "branc", "diurn")):
            dark = False
        else:
            raise ValueError(f"modo escuro ou claro? ('{modo}')")
        await asyncio.to_thread(system.set_theme, dark)
        return "Modo escuro ligado." if dark else "Modo claro ligado."

    @registry.register(
        "recortar_tela", "abre o recorte de tela do Windows para ele selecionar uma área",
        example=("recorta uma parte da tela", "[[acao:recortar_tela]]"),
    )
    def recortar_tela() -> str:
        system.open_uri("ms-screenclip:")
        return "Pode selecionar a área, ela vai pra área de transferência."


_PLACES = (
    ("esquerda", ("esq", "left")), ("direita", ("dir", "right")),
    ("maximizar", ("max", "tela cheia", "tela inteira", "grande")), ("minimizar", ("min", "esconde")),
    ("restaurar", ("rest", "normal", "volta")),
)


def _say_battery(system: System) -> str:
    b = system.battery()
    if b is None:
        return "Esse PC não tem bateria, tá direto na tomada."
    state = ", carregando" if b.charging else ", na tomada" if b.plugged else ""
    return f"A bateria tá em {b.percent}%{state}."


def _say_disk(system: System) -> str:
    drive, free, total = system.disk()
    return f"No disco {drive} sobram {say_size(free)} livres de {say_size(total)}."


def _say_memory(system: System) -> str:
    used, total = system.memory()
    return f"A memória tá em {round(used * 100 / total)}% de uso: {say_size(used)} de {say_size(total)}."


def _say_ip(system: System) -> str:
    ip = system.local_ip()
    return f"O IP do PC na rede é {ip}." if ip else "Não achei o IP: o PC parece estar sem rede."
