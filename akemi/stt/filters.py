"""Filtros de texto transcrito: palavra de ativação, alucinações e eco."""

from __future__ import annotations

import difflib
import re
import time
from collections.abc import Iterable

from akemi.safety.moderation import normalize

# Frases que o Whisper "inventa" em silêncio/ruído (vêm das legendas de treino).
HALLUCINATIONS = {
    "obrigado por assistir",
    "obrigada por assistir",
    "obrigado",
    "legendas pela comunidade amara.org",
    "legenda adriana zanotto",
    "inscreva-se no canal",
    "se inscreva no canal",
    "nao esqueca de se inscrever",
    "tchau",
    "tchau tchau",
    "ate a proxima",
    "thank you for watching",
    "thanks for watching",
    "you",
    "...",
}


def _clean(text: str) -> str:
    return re.sub(r"[^\w\s.-]", "", normalize(text)).strip(" .")


def is_hallucination(text: str) -> bool:
    t = _clean(text)
    if len(t) < 2:
        return True
    if t in HALLUCINATIONS:
        return True
    words = t.split()
    # "ok ok ok ok ok" — repetição degenerada
    return len(words) >= 4 and len(set(words)) == 1


def _phonetic(word: str) -> str:
    """Aproxima a escrita do som (pt-BR): "aquemi" e "akemi" ficam iguais."""
    w = re.sub(r"\W", "", normalize(word))
    w = w.replace("qu", "k").replace("ch", "x").replace("h", "")
    w = re.sub(r"c(?=[aou])", "k", w)
    return w.replace("y", "i").replace("w", "u")


def contains_wake_word(text: str, wake_words: Iterable[str], threshold: float = 0.78) -> bool:
    """Procura a palavra de ativação tolerando erros do Whisper
    ("Aquemi", "a Kemi", "Akimi")."""
    tokens = [_phonetic(t) for t in re.findall(r"\w+", normalize(text))]
    targets = [_phonetic(w) for w in wake_words if w.strip()]
    for size in (1, 2):
        for i in range(len(tokens) - size + 1):
            cand = "".join(tokens[i : i + size])
            for target in targets:
                if cand == target or difflib.SequenceMatcher(None, cand, target).ratio() >= threshold:
                    return True
    return False


def is_echo(text: str, recent_speech: Iterable[tuple[float, str]], window_s: float = 6.0,
            threshold: float = 0.6, now: float | None = None) -> bool:
    """True se o texto parece ser a própria Akemi saindo da caixa de som."""
    now = now or time.time()
    t = _clean(text)
    if not t:
        return False
    for ts, said in recent_speech:
        if now - ts > window_s:
            continue
        s = _clean(said)
        if not s:
            continue
        if t in s or difflib.SequenceMatcher(None, t, s).ratio() >= threshold:
            return True
    return False


# Palavras que podem acompanhar o nome dela sem formar um pedido ("Alô, Akemi").
GREETINGS = {"oi", "ola", "alo", "ei", "hey", "e", "ai", "eai", "opa", "fala", "hm", "hein", "psiu", "ou"}


def is_bare_wake_word(text: str, wake_words: Iterable[str], threshold: float = 0.78) -> bool:
    """True se a frase é só o chamado ("Akemi", "Alô, Akemi") sem pedido nenhum.

    Quem chama e faz uma pausa antes de falar é cortado pelo VAD: o chamado
    chega sozinho e o pedido vem logo depois, em outro trecho.
    """
    tokens = [_phonetic(t) for t in re.findall(r"\w+", normalize(text))]
    targets = [_phonetic(w) for w in wake_words if w.strip()]

    def is_wake(cand: str) -> bool:
        return any(cand == t or difflib.SequenceMatcher(None, cand, t).ratio() >= threshold for t in targets)

    rest, found, i = [], False, 0
    while i < len(tokens):
        if i + 1 < len(tokens) and is_wake(tokens[i] + tokens[i + 1]):  # "a kemi"
            found, i = True, i + 2
        elif is_wake(tokens[i]):
            found, i = True, i + 1
        else:
            rest.append(tokens[i])
            i += 1
    return found and all(t in GREETINGS for t in rest)
