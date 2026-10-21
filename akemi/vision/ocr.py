"""Lê o texto da tela com o Tesseract.

O Tesseract é um programa à parte (não vem pelo pip). No Windows, instale pelo
instalador do UB Mannheim marcando o idioma português; o caminho padrão
``C:\\Program Files\\Tesseract-OCR\\tesseract.exe`` é encontrado sozinho.
"""

from __future__ import annotations

import asyncio
import difflib
import os
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


def prepare_for_ocr(frame: np.ndarray) -> np.ndarray:
    """Cinza e, se a tela for escura, invertida.

    O Tesseract foi treinado com texto escuro em fundo claro; em tema escuro
    (Windows, VS Code, Discord) ele erra muito mais sem a inversão.
    """
    gray = frame[..., :3].mean(axis=2) if frame.ndim == 3 else frame.astype(np.float32)
    if gray.mean() < 128:
        gray = 255 - gray
    return gray.astype(np.uint8)


def lines_from_data(data: dict, min_conf: float = 60.0) -> str:
    """Remonta o texto do ``image_to_data`` linha a linha, sem as palavras de
    baixa confiança (ícones e sombras lidos como letras)."""
    lines: dict[tuple, list[str]] = {}
    for i, word in enumerate(data.get("text", [])):
        word = str(word).strip()
        try:
            conf = float(data["conf"][i])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if not word or conf < min_conf:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
    return "\n".join(" ".join(words) for words in lines.values())


class TesseractOCR:
    def __init__(
        self,
        lang: str = "por+eng",
        cmd: str | None = None,
        tessdata_dir: Path | None = None,
        *,
        scale: int = 2,
        min_conf: float = 60.0,
    ) -> None:
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
        # Idiomas numa pasta do projeto: não precisa de admin para gravar em Program Files.
        # (Via variável de ambiente: o pytesseract repassa aspas do --tessdata-dir
        # literalmente no Windows, e caminhos com espaço quebram sem elas.)
        if tessdata_dir is not None and Path(tessdata_dir).is_dir():
            os.environ["TESSDATA_PREFIX"] = str(Path(tessdata_dir).resolve())
        self.scale = scale
        self.min_conf = min_conf

    def read_sync(self, frame: np.ndarray) -> str:
        from PIL import Image

        img = Image.fromarray(prepare_for_ocr(frame))
        if self.scale != 1:
            img = img.resize((img.width * self.scale, img.height * self.scale), Image.LANCZOS)
        data = self._tess.image_to_data(img, lang=self.lang, output_type=self._tess.Output.DICT)
        return lines_from_data(data, self.min_conf)

    async def read(self, frame: np.ndarray) -> str:
        return await asyncio.to_thread(self.read_sync, frame)
