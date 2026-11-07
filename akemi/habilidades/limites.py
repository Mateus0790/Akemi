"""Os limites das habilidades: onde ela nunca tenta entrar.

Tudo aqui é código, não prompt: o modelo não consegue convencer ninguém a
passar por cima. Três grupos:

- **Jogos com anti-cheat** (Valorant, Fortnite, CS2, Apex...): qualquer mod,
  leitura de memória ou programa mexendo no jogo pode banir a conta do dono.
- **Programas sensíveis**: banco, gerenciador de senhas, a própria Akemi e
  partes do Windows. Não tem o que "participar" ali que valha o risco.
- O que o dono colocar em ``HABILIDADES_BLOQUEADOS``.
"""

from __future__ import annotations

import re
import unicodedata

# Nome do processo (sem .exe, minúsculo) -> por quê.
ANTICHEAT = {
    "valorant": "Valorant", "valorant-win64-shipping": "Valorant", "vgc": "Valorant",
    "fortniteclient-win64-shipping": "Fortnite", "fortnitelauncher": "Fortnite",
    "cs2": "Counter-Strike 2", "csgo": "Counter-Strike",
    "r5apex": "Apex Legends", "r5apex_dx12": "Apex Legends",
    "leagueclient": "League of Legends", "league of legends": "League of Legends",
    "overwatch": "Overwatch", "pubg": "PUBG", "tslgame": "PUBG",
    "rainbowsix": "Rainbow Six", "rainbowsix_be": "Rainbow Six",
    "destiny2": "Destiny 2", "rustclient": "Rust", "eft": "Escape from Tarkov",
    "escapefromtarkov": "Escape from Tarkov", "cod": "Call of Duty", "blackops6": "Call of Duty",
    "fifa": "EA FC", "fc24": "EA FC", "fc25": "EA FC", "fc26": "EA FC",
    "robloxplayerbeta": "Roblox", "gta5": "GTA V", "gta5_enhanced": "GTA V", "fivem": "FiveM",
    "dota2": "Dota 2", "deadlock": "Deadlock", "marvel-win64-shipping": "Marvel Rivals",
    "thefinals": "The Finals", "pathofexile": "Path of Exile", "lostark": "Lost Ark",
    "easyanticheat": "Easy Anti-Cheat", "easyanticheat_eos": "Easy Anti-Cheat",
    "beservice": "BattlEye", "battleye": "BattlEye", "faceit": "FACEIT", "vanguard": "Riot Vanguard",
}

SENSITIVE = {
    "keepass": "gerenciador de senhas", "keepassxc": "gerenciador de senhas",
    "1password": "gerenciador de senhas", "bitwarden": "gerenciador de senhas",
    "lastpass": "gerenciador de senhas", "dashlane": "gerenciador de senhas",
    "warsaw": "módulo de segurança do banco", "core": "módulo de segurança do banco",
    "gbpsv": "módulo de segurança do banco",
    "akemi": "eu mesma", "python": "o Python (pode ser eu)", "pythonw": "o Python (pode ser eu)",
    "explorer": "o Windows", "taskmgr": "o Windows", "regedit": "o Windows", "mmc": "o Windows",
    "cmd": "o terminal", "powershell": "o terminal", "pwsh": "o terminal", "windowsterminal": "o terminal",
    "applicationframehost": "o Windows", "systemsettings": "o Windows", "searchhost": "o Windows",
    "shellexperiencehost": "o Windows", "startmenuexperiencehost": "o Windows", "textinputhost": "o Windows",
}

# Pedaços de nome que já entregam um anti-cheat, mesmo em jogos fora da lista.
ANTICHEAT_HINTS = ("anticheat", "battleye", "vanguard", "equ8", "xigncode", "nprotect", "gameguard")


def program_key(name: str) -> str:
    """ "Minecraft.exe" -> "minecraft": a chave usada nos limites e no estado."""
    t = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().strip()
    t = re.sub(r"\.exe$", "", t)
    return re.sub(r"\s+", " ", t)


def program_slug(name: str) -> str:
    """ "Stardew Valley" -> "stardew-valley": vira o id e o nome da pasta da habilidade."""
    return re.sub(r"[^a-z0-9]+", "-", program_key(name)).strip("-")[:40].strip("-")


def blocked(name: str, extra: list[str] | tuple[str, ...] = ()) -> str | None:
    """Por que ela não pode criar habilidade para esse programa (ou None se pode)."""
    key = program_key(name).replace("-", " ")
    if not key:
        return "programa sem nome"
    if key in {program_key(e).replace("-", " ") for e in extra}:
        return "você bloqueou esse programa"
    joined = key.replace(" ", "-")
    for k in (key, joined):
        if k in ANTICHEAT:
            return f"{ANTICHEAT[k]} tem anti-cheat: mexer nele pode banir a sua conta"
    key = joined if joined in SENSITIVE else key
    if any(h in key.replace(" ", "").replace("-", "") for h in ANTICHEAT_HINTS):
        return "parece ter anti-cheat: mexer nele pode banir a sua conta"
    if key in SENSITIVE:
        return f"é {SENSITIVE[key]}: não mexo nisso"
    return None
