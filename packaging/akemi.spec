# -*- mode: python ; coding: utf-8 -*-
"""Receita do PyInstaller para o Akemi.exe.

    pip install -e ".[exe]"           # + os extras que quiser dentro do .exe
    pyinstaller packaging/akemi.spec --noconfirm

Sai em dist/Akemi/ (Akemi.exe + a pasta _internal). Só entra o que estiver
instalado no venv: sem o extra "tts", por exemplo, o .exe fica sem o Piper.
"""

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "akemi" / "brain" / "*.yaml"), "akemi/brain"),
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "LICENSE"), "."),
]
binaries = []
# Quase tudo na Akemi é importado só quando o recurso liga, então o
# PyInstaller não acharia sozinho.
hiddenimports = collect_submodules("akemi")

# Pacotes opcionais com DLLs, modelos ou dados: entram inteiros se existirem.
# (Os outros, como webrtcvad, Pillow, websockets e pywin32, o PyInstaller já
# sabe empacotar sozinho.)
OPTIONAL = [
    "faster_whisper", "ctranslate2", "av", "tokenizers",
    "onnxruntime", "piper", "kokoro_onnx", "espeakng_loader", "phonemizer",
    "sherpa_onnx", "_sounddevice_data", "pyaudiowpatch",
]
for name in OPTIONAL:
    if importlib.util.find_spec(name) is None:
        continue
    d, b, h = collect_all(name)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "akemi" / "exe.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "hooks")],
    excludes=["pytest", "_pytest", "llama_cpp", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Akemi",
    console=True,  # ela conversa pelo terminal
    upx=False,     # UPX quebra DLLs do onnxruntime e assusta antivírus
)
coll = COLLECT(exe, a.binaries, a.datas, name="Akemi", upx=False)
