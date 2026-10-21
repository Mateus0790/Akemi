"""Detecta quando a tela mudou de verdade, sem rodar OCR a cada captura.

Cada captura vira uma miniatura em tons de cinza. Comparar miniaturas é barato
e já filtra o cursor piscando, relógios e pequenas animações. Só quando a tela
muda bastante *e depois para de mudar* (a página terminou de carregar, a janela
terminou de abrir) é que vale ler o texto dela.
"""

from __future__ import annotations

import numpy as np


def thumbnail(frame: np.ndarray, step: int = 8) -> np.ndarray:
    """Miniatura em cinza (``uint8``) de um frame RGB/RGBA/cinza ``(H, W[, C])``.

    Amostra 1 a cada ``step`` pixels antes de converter, então custa pouco
    mesmo em 4K.
    """
    small = frame[::step, ::step]
    if small.ndim == 3:
        small = small[..., :3].mean(axis=2)
    return small.astype(np.uint8)


def changed_fraction(a: np.ndarray, b: np.ndarray, pixel_delta: int = 24) -> float:
    """Fração dos pixels que mudaram mais que ``pixel_delta`` (0–255)."""
    if a.shape != b.shape:
        return 1.0  # resolução mudou: trata como tela nova
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(np.mean(diff > pixel_delta))


class ChangeDetector:
    """Diz quando há uma tela nova e estável para ler.

    ``update`` devolve ``True`` uma vez por mudança: quando o frame atual
    difere do último *reportado* em mais de ``threshold`` e é praticamente
    igual ao frame anterior (a tela assentou). Um vídeo tocando em tela cheia
    nunca assenta, então não fica disparando OCR à toa.
    """

    def __init__(self, threshold: float = 0.06, pixel_delta: int = 24) -> None:
        self.threshold = threshold
        self.pixel_delta = pixel_delta
        self._prev: np.ndarray | None = None
        self._reported: np.ndarray | None = None

    def update(self, thumb: np.ndarray) -> bool:
        prev, self._prev = self._prev, thumb
        if prev is None:
            return False
        if changed_fraction(prev, thumb, self.pixel_delta) > self.threshold:
            return False  # ainda mudando
        if self._reported is not None and (
            changed_fraction(self._reported, thumb, self.pixel_delta) <= self.threshold
        ):
            return False  # é a mesma tela que já lemos
        self._reported = thumb
        return True

    def reset(self) -> None:
        """Esquece a última tela lida (a próxima tela estável será reportada)."""
        self._reported = None
