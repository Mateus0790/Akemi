"""Os jogos. Cada um entende a jogada em texto e responde do jeito dela.

``play`` devolve um :class:`Turn` ou None quando o texto não é uma jogada (aí a
conversa segue normal com o modelo).
"""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass


def plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", text))


NUMBER_WORDS = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14,
    "catorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19,
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70,
    "oitenta": 80, "noventa": 90, "cem": 100,
}


def number_in(text: str) -> int | None:
    """O primeiro número dito: "37", "trinta e sete", "cinco"."""
    t = plain(text)
    if m := re.search(r"\b\d{1,3}\b", t):
        return int(m.group())
    words = t.split()
    for i, w in enumerate(words):
        if w in NUMBER_WORDS:
            value = NUMBER_WORDS[w]
            if value >= 20 and i + 2 < len(words) and words[i + 1] == "e" and words[i + 2] in NUMBER_WORDS:
                unit = NUMBER_WORDS[words[i + 2]]
                if unit < 10:
                    value += unit
            return value
    return None


@dataclass
class Turn:
    reply: str  # o que ela fala
    board: str = ""  # o que aparece no console (tabuleiro)
    over: bool = False
    winner: str = ""  # "dono" | "akemi" | "empate" | "" (desistiu)


class Game:
    name = "jogo"
    title = "um jogo"

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    def start(self) -> Turn:  # pragma: no cover - interface
        raise NotImplementedError

    def play(self, text: str) -> Turn | None:  # pragma: no cover - interface
        raise NotImplementedError

    def status(self) -> str:
        return ""


# ================================================================ velha
LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))


def winner_of(board: list[str]) -> str:
    for a, b, c in LINES:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]
    return "empate" if " " not in board else ""


def minimax(board: list[str], me: str, turn: str) -> int:
    win = winner_of(board)
    if win == me:
        return 1
    if win == "empate":
        return 0
    if win:
        return -1
    scores = []
    for i in range(9):
        if board[i] == " ":
            board[i] = turn
            scores.append(minimax(board, me, "O" if turn == "X" else "X"))
            board[i] = " "
    return max(scores) if turn == me else min(scores)


def parse_cell(text: str) -> int | None:
    """ "5", "cinco", "centro", "canto de cima à esquerda", "meio de baixo" -> 0..8."""
    t = plain(text)
    if m := re.fullmatch(r"(?:(?:no|na|o|a|casa|posicao|numero|joga|jogo|eu jogo|vou no|vou na)\s+)*([1-9])", t):
        return int(m.group(1)) - 1
    words = t.split()
    if len(words) > 7:
        return None  # frase comprida é conversa, não jogada
    nums = [NUMBER_WORDS[w] for w in words if w in NUMBER_WORDS and 1 <= NUMBER_WORDS[w] <= 9]
    if len(nums) == 1 and len(words) <= 4:
        return nums[0] - 1
    row = 0 if re.search(r"\b(cima|superior|topo|alto)\b", t) else 2 if re.search(r"\b(baixo|inferior)\b", t) else None
    col = 0 if re.search(r"\besquerd", t) else 2 if re.search(r"\bdireit", t) else None
    middle = re.search(r"\b(meio|centro|central)\b", t)
    if row is None and col is None:
        return 4 if middle else None
    if row is None:
        row = 1
    if col is None:
        col = 1
    return row * 3 + col


class TicTacToe(Game):
    name = "velha"
    title = "jogo da velha"
    CORNERS = (0, 2, 6, 8)

    def __init__(self, rng: random.Random | None = None, mistakes: float = 0.25) -> None:
        super().__init__(rng)
        self.mistakes = mistakes  # ela erra de vez em quando, para dar para ganhar
        self.board = [" "] * 9
        self.owner_starts = True

    def render(self) -> str:
        cells = [c if c != " " else str(i + 1) for i, c in enumerate(self.board)]
        rows = [" " + " | ".join(cells[r * 3:r * 3 + 3]) for r in range(3)]
        return "\n---+---+---\n".join(rows)

    def status(self) -> str:
        mine = [str(i + 1) for i, c in enumerate(self.board) if c == "O"]
        yours = [str(i + 1) for i, c in enumerate(self.board) if c == "X"]
        free = [str(i + 1) for i, c in enumerate(self.board) if c == " "]
        return (f"você tem {', '.join(yours) or 'nada'}; eu tenho {', '.join(mine) or 'nada'}; "
                f"livres: {', '.join(free)}")

    def start(self) -> Turn:
        self.board = [" "] * 9
        return Turn("Jogo da velha! Você é o X e começa. As casas vão de 1 a 9, da esquerda pra direita, "
                    "começando em cima. Diz o número.", self.render())

    def _pick(self) -> int:
        free = [i for i in range(9) if self.board[i] == " "]
        # Ganha se der; bloqueia se precisar. Fora isso, às vezes "se distrai".
        for who in ("O", "X"):
            for i in free:
                self.board[i] = who
                done = winner_of(self.board) == who
                self.board[i] = " "
                if done:
                    return i
        if self.rng.random() < self.mistakes:
            return self.rng.choice(free)
        best, best_score = [], -2
        for i in free:
            self.board[i] = "O"
            score = minimax(self.board, "O", "X")
            self.board[i] = " "
            if score > best_score:
                best, best_score = [i], score
            elif score == best_score:
                best.append(i)
        return self.rng.choice(best)

    def play(self, text: str) -> Turn | None:
        if re.search(r"\b(tabuleiro|como (ta|esta)|placar)\b", plain(text)):
            return Turn(self.status()[:1].upper() + self.status()[1:] + ".", self.render())
        cell = parse_cell(text)
        if cell is None:
            return None
        if self.board[cell] != " ":
            return Turn(f"A {cell + 1} já tá ocupada. Escolhe outra.", self.render())
        self.board[cell] = "X"
        if win := winner_of(self.board):
            return self._end(win)
        mine = self._pick()
        self.board[mine] = "O"
        if win := winner_of(self.board):
            return self._end(win, mine)
        line = self.rng.choice(("Joguei no {n}. Sua vez.", "Fico com a {n}. Vai.", "{n} pra mim. E aí?"))
        return Turn(line.format(n=mine + 1), self.render())

    def _end(self, win: str, mine: int | None = None) -> Turn:
        played = f"Joguei no {mine + 1}. " if mine is not None else ""
        if win == "X":
            return Turn("Ganhou. Tá, foi bom. Revanche?", self.render(), True, "dono")
        if win == "O":
            return Turn(played + self.rng.choice(("E ganhei! Três em linha.", "Velha é comigo mesmo. Ganhei.")),
                        self.render(), True, "akemi")
        return Turn(played + "Deu velha. Empate.", self.render(), True, "empate")


# ===================================================== pedra-papel-tesoura
MOVES = ("pedra", "papel", "tesoura")
BEATS = {"pedra": "tesoura", "papel": "pedra", "tesoura": "papel"}
HOW = {("pedra", "tesoura"): "pedra quebra tesoura", ("papel", "pedra"): "papel embrulha pedra",
       ("tesoura", "papel"): "tesoura corta papel"}


class RockPaperScissors(Game):
    name = "pedra"
    title = "pedra, papel e tesoura"

    def __init__(self, rng: random.Random | None = None, best_of: int = 3) -> None:
        super().__init__(rng)
        self.need = best_of // 2 + 1
        self.score = {"dono": 0, "akemi": 0}
        self.mine = self.rng.choice(MOVES)  # ela escolhe antes de ouvir a sua

    def status(self) -> str:
        return f"{self.score['dono']} a {self.score['akemi']} (você primeiro)"

    def start(self) -> Turn:
        self.score = {"dono": 0, "akemi": 0}
        self.mine = self.rng.choice(MOVES)
        return Turn(f"Pedra, papel e tesoura, melhor de {self.need * 2 - 1}. Já escolhi. Fala a sua!")

    def play(self, text: str) -> Turn | None:
        said = [m for m in MOVES if re.search(rf"\b{m}\b", plain(text))]
        if len(said) != 1:
            return None if not said else Turn("Uma de cada vez! Pedra, papel ou tesoura?")
        yours, mine = said[0], self.mine
        self.mine = self.rng.choice(MOVES)  # já escolhe a da próxima rodada
        if yours == mine:
            return Turn(f"Também tirei {mine}. Empatou essa, de novo!", self.status())
        if BEATS[yours] == mine:
            self.score["dono"] += 1
            point = f"Tirei {mine}. {HOW[(yours, mine)].capitalize()}, ponto seu."
        else:
            self.score["akemi"] += 1
            point = f"Tirei {mine}. {HOW[(mine, yours)].capitalize()}, ponto meu."
        if self.score["dono"] >= self.need:
            return Turn(f"{point} Você ganhou de {self.score['dono']} a {self.score['akemi']}. Revanche?",
                        self.status(), True, "dono")
        if self.score["akemi"] >= self.need:
            return Turn(f"{point} Ganhei de {self.score['akemi']} a {self.score['dono']}! Quer outra?",
                        self.status(), True, "akemi")
        lead = ("Tá empatado" if self.score["dono"] == self.score["akemi"]
                else f"{max(self.score.values())} a {min(self.score.values())} pra "
                     f"{'você' if self.score['dono'] > self.score['akemi'] else 'mim'}")
        return Turn(f"{point} {lead}. Próxima!", self.status())


# ================================================================ forca
WORDS = {
    "fruta": ("abacaxi", "banana", "morango", "melancia", "jabuticaba", "goiaba", "maracujá", "pitanga",
              "caju", "acerola"),
    "animal": ("cachorro", "girafa", "elefante", "tartaruga", "borboleta", "pinguim", "jacaré", "capivara",
               "tamanduá", "papagaio"),
    "coisa de casa": ("teclado", "cadeira", "travesseiro", "geladeira", "ventilador", "liquidificador",
                      "chuveiro", "tapete"),
    "comida": ("chocolate", "brigadeiro", "pipoca", "coxinha", "feijoada", "pastel", "lasanha", "pamonha"),
    "coisa de jogo": ("controle", "fliperama", "joystick", "tabuleiro", "videogame", "personagem"),
}
LETTER_NAMES = {
    "a": "a", "be": "b", "ce": "c", "de": "d", "e": "e", "efe": "f", "ge": "g", "aga": "h", "i": "i",
    "jota": "j", "ka": "k", "ca": "k", "ele": "l", "eme": "m", "ene": "n", "o": "o", "pe": "p", "que": "q",
    "erre": "r", "esse": "s", "te": "t", "u": "u", "ve": "v", "dablio": "w", "xis": "x",
    "ipsilon": "y", "ze": "z",
}
MAX_MISSES = 6


def guess_in(text: str) -> tuple[str, str] | None:
    """("letra", "a") ou ("palavra", "banana"). None se não parece um chute."""
    t = plain(text)
    if m := re.search(r"\bletra\s+([a-z]+)\b", t):
        name = m.group(1)
        letter = name if len(name) == 1 else LETTER_NAMES.get(name)
        return ("letra", letter) if letter else None
    if len(t) == 1 and t.isalpha():
        return "letra", t
    if t in LETTER_NAMES and len(t) > 1:
        return "letra", LETTER_NAMES[t]
    if m := re.fullmatch(r"(?:(?:e|eh|a palavra e|acho que e|chuto|sera que e|e a palavra)\s+)*([a-z]{2,})", t):
        return "palavra", m.group(1)
    return None


class Hangman(Game):
    name = "forca"
    title = "forca"

    def __init__(self, rng: random.Random | None = None) -> None:
        super().__init__(rng)
        self.word = ""
        self.key = ""
        self.hint = ""
        self.found: set[str] = set()
        self.missed: list[str] = []

    def start(self) -> Turn:
        self.hint = self.rng.choice(sorted(WORDS))
        self.word = self.rng.choice(WORDS[self.hint])
        self.key = plain(self.word).replace(" ", "")
        self.found, self.missed = set(), []
        return Turn(f"Forca! Pensei numa palavra: é {self.hint}, com {len(self.key)} letras. "
                    f"Você pode errar {MAX_MISSES} vezes. Fala uma letra ou chuta a palavra.", self.render())

    def masked(self) -> str:
        return " ".join(c.upper() if plain(c) in self.found else "_" for c in self.word)

    def render(self) -> str:
        misses = ", ".join(self.missed).upper() or "nenhum"
        return f"{self.masked()}   erros: {misses} ({len(self.missed)}/{MAX_MISSES})"

    def status(self) -> str:
        left = sum(1 for c in self.key if c not in self.found)
        return f"faltam {left} letras e você errou {len(self.missed)} de {MAX_MISSES}"

    def play(self, text: str) -> Turn | None:
        guess = guess_in(text)
        if guess is None:
            return None
        kind, value = guess
        if kind == "palavra":
            if value == self.key:
                self.found = set(self.key)
                return Turn(f"É {self.word}! Acertou na mosca.", self.render(), True, "dono")
            if len(value) != len(self.key):
                return None  # provavelmente não era chute, era conversa
            self.missed.append(value)
            if len(self.missed) >= MAX_MISSES:
                return self._lost()
            return Turn(f"Não é {value}. Errou, conta como erro: {len(self.missed)} de {MAX_MISSES}.", self.render())
        if value in self.found or value in self.missed:
            return Turn(f"O {value.upper()} você já falou.", self.render())
        count = self.key.count(value)
        if count == 0:
            self.missed.append(value)
            if len(self.missed) >= MAX_MISSES:
                return self._lost()
            return Turn(f"Não tem {value.upper()}. Erros: {len(self.missed)} de {MAX_MISSES}.", self.render())
        self.found.add(value)
        if all(c in self.found for c in self.key):
            return Turn(f"Tem! E fechou: {self.word}. Ganhou!", self.render(), True, "dono")
        times = {1: "um", 2: "dois", 3: "três", 4: "quatro", 5: "cinco"}.get(count, str(count))
        return Turn(f"Tem {times} {value.upper()}. {self.status()[:1].upper()}{self.status()[1:]}.", self.render())

    def _lost(self) -> Turn:
        return Turn(f"Enforcou! A palavra era {self.word}. Quer tentar outra?", self.render(), True, "akemi")


# ========================================================== adivinhação
class GuessNumber(Game):
    name = "adivinhacao"
    title = "adivinhação"

    def __init__(self, rng: random.Random | None = None, top: int = 100, tries: int = 7) -> None:
        super().__init__(rng)
        self.top, self.max_tries = top, tries
        self.secret = 0
        self.tries = 0

    def status(self) -> str:
        return f"você já tentou {self.tries} de {self.max_tries} vezes"

    def start(self) -> Turn:
        self.secret = self.rng.randint(1, self.top)
        self.tries = 0
        return Turn(f"Pensei num número de 1 a {self.top}. Você tem {self.max_tries} chances. Chuta!")

    def play(self, text: str) -> Turn | None:
        n = number_in(text)
        if n is None:
            return None
        if not 1 <= n <= self.top:
            return Turn(f"É de 1 a {self.top}, espertinho.")
        self.tries += 1
        if n == self.secret:
            return Turn(f"Acertou! Era {n}, em {self.tries} tentativa{'s' if self.tries > 1 else ''}.",
                        over=True, winner="dono")
        if self.tries >= self.max_tries:
            return Turn(f"Acabaram as chances! Era {self.secret}. Quer outra?", over=True, winner="akemi")
        hint = "Mais alto." if n < self.secret else "Mais baixo."
        close = " Tá quente!" if abs(n - self.secret) <= 3 else ""
        return Turn(f"{hint}{close} Restam {self.max_tries - self.tries}.")


GAMES: dict[str, type[Game]] = {g.name: g for g in (TicTacToe, RockPaperScissors, Hangman, GuessNumber)}
