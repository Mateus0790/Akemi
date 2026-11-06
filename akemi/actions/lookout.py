"""Vigia da tela: "me avisa quando aparecer 'download concluído' na tela".

Ideia do "Lookout" do Capri, feita do jeito da Akemi: ela confere o texto que já
lê da tela (OCR e, com modelo de visão, a descrição da cena) contra as frases que
você pediu. Aceita pequenos erros de OCR e ignora acentos e maiúsculas.

O texto da tela é ``Trust.PUBLIC``: aqui ele só é comparado com a sua frase.
Nada do que aparece na tela vira ordem; o aviso dela é sempre a frase que você pediu.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher

from .registry import ActionRegistry

MAX_WATCHES = 8
MAX_PHRASE = 80
FUZZY = 0.86  # quão parecido o trecho precisa ser (OCR erra letras)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def contains(haystack: str, needle: str) -> bool:
    """A frase aparece no texto? Exata (sem acento) ou quase igual, palavra por palavra."""
    if not needle:
        return False
    if f" {needle} " in f" {haystack} ":
        return True
    words, target = haystack.split(), needle.split()
    n = len(target)
    if n == 0 or len(words) < n or len(needle) < 4:
        return False
    for size in {n, n + 1, max(1, n - 1)}:
        for i in range(len(words) - size + 1):
            window = " ".join(words[i:i + size])
            if abs(len(window) - len(needle)) <= max(2, len(needle) // 5) and \
                    SequenceMatcher(None, window, needle).ratio() >= FUZZY:
                return True
    return False


@dataclass
class Watch:
    id: int
    phrase: str
    key: str
    created_at: float


class Lookout:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.watches: list[Watch] = []
        self._next = 1

    def add(self, phrase: str) -> Watch:
        phrase = " ".join(str(phrase).split()).strip(" \"'“”.")
        key = normalize(phrase)
        if not key:
            raise ValueError("vigiar o quê? Me diz o texto que vai aparecer")
        if len(phrase) > MAX_PHRASE:
            raise ValueError("frase grande demais para vigiar; me diz só um pedaço")
        for w in self.watches:
            if w.key == key:
                return w
        if len(self.watches) >= MAX_WATCHES:
            raise ValueError(f"já estou vigiando {MAX_WATCHES} coisas")
        watch = Watch(self._next, phrase, key, self.clock())
        self._next += 1
        self.watches.append(watch)
        return watch

    def stop(self, phrase: str = "") -> list[Watch]:
        key = normalize(phrase)
        gone = [w for w in self.watches if not key or key in w.key or w.key in key]
        self.watches = [w for w in self.watches if w not in gone]
        return gone

    def check(self, seen: str) -> list[Watch]:
        """Confere um texto visto. Devolve (e tira da lista) o que apareceu."""
        if not self.watches or not seen:
            return []
        text = normalize(seen)
        hits = [w for w in self.watches if contains(text, w.key)]
        self.watches = [w for w in self.watches if w not in hits]
        return hits

    def describe(self) -> str:
        if not self.watches:
            return "Não estou vigiando nada na tela."
        return "Estou vigiando a tela esperando: " + "; ".join(f'"{w.phrase}"' for w in self.watches) + "."


def register_lookout_actions(registry: ActionRegistry, lookout: Lookout, can_see: Callable[[], bool]) -> None:
    @registry.register(
        "vigiar_tela", "fica de olho na tela e avisa o dono quando um texto aparecer (download, fila, mensagem)",
        params={"texto": "o texto que vai aparecer na tela"},
        example=("me avisa quando aparecer download concluído",
                 '[[acao:vigiar_tela {"texto": "download concluído"}]]'),
    )
    def vigiar_tela(texto: str) -> str:
        if not can_see():
            return "Pra vigiar a tela eu preciso estar olhando ela. Me liga com --tela."
        watch = lookout.add(texto)
        return f'Deixa comigo, te aviso quando aparecer "{watch.phrase}".'

    @registry.register("parar_vigia", "para de vigiar a tela (um texto ou tudo)",
                       params={"texto": "o que parar de vigiar (vazio = tudo)"})
    def parar_vigia(texto: str = "") -> str:
        gone = lookout.stop(texto)
        if not gone:
            return "Não estava vigiando isso."
        return "Parei de vigiar " + ", ".join(f'"{w.phrase}"' for w in gone) + "."

    @registry.register("listar_vigias", "diz o que você está vigiando na tela", speak_result=True)
    def listar_vigias() -> str:
        return lookout.describe()
