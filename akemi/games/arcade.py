"""Abre, conduz e encerra os minijogos.

- "vamos jogar jogo da velha", "bora jogar forca": abre o jogo na hora (sem modelo).
- Com o jogo aberto, cada jogada vai direto para ele.
- "para o jogo", "desisto", "chega": encerra. Depois de um fim, "de novo" ou
  "revanche" abre outra partida.
- O placar da sessão fica guardado; o resultado de cada partida vai para a memória.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable, Iterable

from akemi.actions.registry import ActionRegistry
from akemi.brain.mind import ShortcutReply
from akemi.core.events import Event, Trust

from .games import GAMES, Game, Turn, plain

AGAIN_WINDOW_S = 120.0
START_RE = re.compile(r"\b(jog\w*|bora|vamos|quero|partida|brincar|desafio|desafi\w+)\b")
QUIT_RE = re.compile(r"^(?:(?:para|pare|parar|sai|sair|fecha|fechar|encerra|encerrar|termina|terminar|acaba|acabar)"
                     r"(?: (?:o|do|de|com o|esse))? ?(?:jogo|partida|jogar)?|desisto|chega|cansei|nao quero mais"
                     r"(?: jogar)?)$")
AGAIN_RE = re.compile(r"^(?:(?:de novo|outra|outra vez|mais uma|revanche|bora|vamos|quero|sim|pode|joga)"
                      r"(?: (?:de novo|outra|mais uma|partida|vez|ai))*)$")
GAME_WORDS = (
    ("velha", re.compile(r"\b(jogo da velha|velha|tic tac toe)\b")),
    ("forca", re.compile(r"\bforca\b")),
    ("pedra", re.compile(r"\b(pedra papel|jokenpo|jo ken po|ppt)\b")),
    ("adivinhacao", re.compile(r"\b(adivinha\w*|numero secreto)\b")),
)


def which_game(text: str) -> str | None:
    t = plain(text)
    for name, rx in GAME_WORDS:
        if rx.search(t):
            return name
    return None


class Arcade:
    def __init__(
        self,
        *,
        on_board: Callable[[str], None] | None = None,
        on_result: Callable[[str, str], None] | None = None,
        rng: random.Random | None = None,
        wake_words: Iterable[str] = ("akemi",),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.on_board = on_board
        self.on_result = on_result  # (título do jogo, resultado) para a memória
        self.rng = rng or random.Random()
        self.wake_words = {plain(w) for w in wake_words}
        self.clock = clock
        self.game: Game | None = None
        self.last: str = ""
        self.over_at = -1e9
        self.score: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------ partidas
    def start(self, name: str) -> str:
        if name not in GAMES:
            raise ValueError("eu sei jogar jogo da velha, forca, pedra-papel-tesoura e adivinhação")
        self.game = GAMES[name](self.rng)
        self.last = name
        return self._show(self.game.start())

    def stop(self) -> str:
        if self.game is None:
            return "A gente não tá jogando nada."
        title = self.game.title
        self.game = None
        self.over_at = -1e9
        return f"Beleza, parei a partida de {title}. {self._scoreline(self.last)}".strip()

    def _show(self, turn: Turn) -> str:
        if turn.board and self.on_board is not None:
            self.on_board(turn.board)
        if turn.over and self.game is not None:
            title = self.game.title
            board = self.score.setdefault(self.last, {"dono": 0, "akemi": 0, "empate": 0})
            if turn.winner in board:
                board[turn.winner] += 1
            if self.on_result is not None and turn.winner:
                self.on_result(title, turn.winner)
            self.game = None
            self.over_at = self.clock()
        return turn.reply

    def _scoreline(self, name: str) -> str:
        board = self.score.get(name)
        if not board or not (board["dono"] or board["akemi"]):
            return ""
        return f"Placar de hoje: você {board['dono']}, eu {board['akemi']}."

    def _words(self, text: str) -> str:
        words = plain(text).split()
        while words and words[0] in self.wake_words:
            words.pop(0)
        return " ".join(words)

    # ------------------------------------------------------------ atalho
    async def shortcut(self, text: str, event: Event) -> ShortcutReply | None:
        """Para a mente: jogadas e pedidos de jogo, só do dono."""
        if event.trust < Trust.OWNER:
            return None
        said = self._words(text)
        if self.game is not None:
            if QUIT_RE.match(said):
                return ShortcutReply(self.stop())
            turn = self.game.play(said)
            if turn is None:
                return None  # não era jogada: conversa normal (o jogo continua aberto)
            reply = self._show(turn)
            if turn.over and (line := self._scoreline(self.last)) and self.score[self.last]["dono"] + \
                    self.score[self.last]["akemi"] > 1:
                reply = f"{reply} {line}"
            return ShortcutReply(reply, remember=False)
        if self.last and self.clock() - self.over_at < AGAIN_WINDOW_S and AGAIN_RE.match(said):
            return ShortcutReply(self.start(self.last), remember=False)
        name = which_game(said)
        if name and START_RE.search(said):
            return ShortcutReply(self.start(name), remember=False)
        return None

    def describe_for_prompt(self) -> str:
        if self.game is None:
            return ""
        status = self.game.status()
        return (f"Você está jogando {self.game.title} com o dono, por voz. As jogadas dele vão direto para "
                f"o jogo; se ele falar de outra coisa, converse normalmente.{f' Situação: {status}.' if status else ''}")


def register_game_actions(registry: ActionRegistry, arcade: Arcade) -> None:
    @registry.register(
        "jogar", "começa um minijogo com o dono por voz: velha (jogo da velha), forca, pedra "
        "(pedra-papel-tesoura) ou adivinhacao (número de 1 a 100)",
        params={"jogo": "velha | forca | pedra | adivinhacao"},
        example=("tô entediado, vamos jogar alguma coisa?", '[[acao:jogar {"jogo": "forca"}]]'),
        speak_result=True,
    )
    def jogar(jogo: str) -> str:
        name = which_game(jogo) or plain(jogo).replace(" ", "")
        return arcade.start(name)

    @registry.register("parar_jogo", "encerra o minijogo aberto")
    def parar_jogo() -> str:
        return arcade.stop()
