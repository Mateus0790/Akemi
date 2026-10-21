"""Lê o texto da tela com o Tesseract.

O Tesseract é um programa à parte (não vem pelo pip). No Windows, instale pelo
instalador do UB Mannheim marcando o idioma português; o caminho padrão
``C:\\Program Files\\Tesseract-OCR\\tesseract.exe`` é encontrado sozinho.
"""

from __future__ import annotations

import asyncio
import difflib
import re
import shutil
import sys
from pathlib import Path

import numpy as np

WINDOWS_DEFAULT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")

_WS = re.compile(r"[ \t]+")
_ALNUM = re.compile(r"[0-9A-Za-zÀ-ÿ]")


def clean_ocr_text(raw: str, max_chars: int = 1500) -> str:
    """Remove o lixo típico do OCR: linhas vazias, ícones lidos como símbolos,
    letras soltas. Mantém a ordem das linhas."""
    lines = []
    for line in raw.splitlines():
        line = _WS.sub(" ", line).strip()
        letters = len(_ALNUM.findall(line))
        # Linhas curtas demais ou que são quase só símbolos ("|=~ »") são ruído.
        if letters < 3 or letters < len(line) * 0.5:
            continue
        lines.append(line)
    text = "\n".join(lines)
    return text[:max_chars]


def similarity(a: str, b: str) -> float:
    """0–1. Usado para não reportar a mesma tela duas vezes (o OCR varia um pouco)."""
    if not a or not b:
        return 1.0 if a == b else 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


class TesseractOCR:
    def __init__(self, lang: str = "por+eng", cmd: str | None = None) -> None:
        try:
            import pytesseract
            from PIL import Image  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                'OCR precisa de pytesseract e Pillow: pip install -e ".[vision]"'
            ) from exc
        exe = cmd or shutil.which("tesseract")
        if not exe and sys.platform == "win32" and WINDOWS_DEFAULT.exists():
            exe = str(WINDOWS_DEFAULT)
        if not exe:
            raise RuntimeError(
                "Tesseract não encontrado. Instale-o (com o idioma português) "
                "ou defina TESSERACT_CMD no .env."
            )
        pytesseract.pytesseract.tesseract_cmd = exe
        self._tess = pytesseract
        self.lang = lang

    def read_sync(self, frame: np.ndarray) -> str:
        from PIL import Image

        img = Image.fromarray(frame[..., :3] if frame.ndim == 3 else frame).convert("L")
        return self._tess.image_to_string(img, lang=self.lang)

    async def read(self, frame: np.ndarray) -> str:
        return await asyncio.to_thread(self.read_sync, frame)
