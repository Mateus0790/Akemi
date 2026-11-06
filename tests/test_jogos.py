"""Minijogos por voz."""

import random

import pytest

from akemi.core.events import Event, Trust
from akemi.games.arcade import Arcade, which_game
from akemi.games.games import (
    GuessNumber, Hangman, RockPaperScissors, TicTacToe, guess_in, number_in, parse_cell, winner_of,
)

OWNER = Event("owner.message", trust=Trust.OWNER)


# ------------------------------------------------------------------ velha
@pytest.mark.parametrize("text, cell", [
    ("5", 4), ("no 1", 0), ("cinco", 4), ("nove", 8), ("centro", 4), ("meio", 4),
    ("canto de cima à esquerda", 0), ("em cima no meio", 1), ("canto de baixo direito", 8),
    ("meio de baixo", 7), ("na esquerda", 3),
])
def test_parse_cell(text, cell):
    assert parse_cell(text) == cell


def test_parse_cell_ignores_conversation():
    assert parse_cell("que dia lindo") is None
    assert parse_cell("então eu tava lá em cima da casa da minha vó ontem") is None


def test_she_wins_when_she_can_and_blocks_when_she_must():
    g = TicTacToe(random.Random(0), mistakes=0)
    g.start()
    g.board = ["O", "O", " ", "X", "X", " ", " ", " ", " "]
    assert g._pick() == 2  # ganha
    g.board = ["X", "X", " ", " ", "O", " ", " ", " ", " "]
    assert g._pick() == 2  # bloqueia


def test_perfect_akemi_never_loses():
    for seed in range(15):
        rng = random.Random(seed)
        g = TicTacToe(rng, mistakes=0)
        g.start()
        turn = None
        while turn is None or not turn.over:
            free = [i for i, c in enumerate(g.board) if c == " "]
            turn = g.play(str(rng.choice(free) + 1))
        assert turn.winner in ("akemi", "empate")


def test_tictactoe_turns_and_errors():
    g = TicTacToe(random.Random(1), mistakes=0)
    assert "1 a 9" in g.start().reply
    t = g.play("5")
    assert t.reply and "X" in t.board and "O" in t.board
    t = g.play("5")
    assert t.reply == "A 5 já tá ocupada. Escolhe outra."
    assert g.play("bom dia") is None
    assert g.play("como tá o tabuleiro").reply.startswith("Você tem 5")
    assert winner_of(list("XXXOO    ")) == "X" and winner_of(list("XOXXOOOXX")) == "empate"


# ------------------------------------------------------- pedra-papel-tesoura
def test_rock_paper_scissors_best_of_three():
    g = RockPaperScissors(random.Random(3))
    g.start()
    assert g.play("tá difícil") is None
    assert g.play("pedra e papel").reply.startswith("Uma de cada vez")
    turn = None
    rounds = 0
    while turn is None or not turn.over:
        planned = g.mine  # ela já escolheu antes de ouvir
        turn = g.play("tesoura")
        rounds += 1
        assert turn.reply.startswith(f"Tirei {planned}") or turn.reply.startswith(f"Também tirei {planned}")
        assert rounds < 30
    assert turn.winner in ("dono", "akemi") and max(g.score.values()) == 2


# ------------------------------------------------------------------ forca
@pytest.mark.parametrize("text, guess", [
    ("a", ("letra", "a")), ("A.", ("letra", "a")), ("letra e", ("letra", "e")), ("é a letra erre", ("letra", "r")),
    ("efe", ("letra", "f")), ("banana", ("palavra", "banana")), ("é banana", ("palavra", "banana")),
    ("a palavra é jacaré", ("palavra", "jacare")), ("que difícil", None), ("letra nada", None),
])
def test_guess_in(text, guess):
    assert guess_in(text) == guess


def test_hangman_win_and_accents():
    g = Hangman(random.Random(0))
    g.start()
    g.word, g.key = "maracujá", "maracuja"
    t = g.play("a")
    assert t.reply == "Tem três A. Faltam 5 letras e você errou 0 de 6."
    assert t.board.startswith("_ A _ A _ _ _ Á")  # o acento conta como a mesma letra
    assert g.play("a").reply == "O A você já falou."
    t = g.play("z")
    assert t.reply.startswith("Não tem Z") and len(g.missed) == 1
    assert g.play("nossa") is None  # tamanho diferente: conversa, não chute
    t = g.play("maracuja")
    assert t.over and t.winner == "dono" and "maracujá" in t.reply


def test_hangman_loses_after_six_misses():
    g = Hangman(random.Random(0))
    g.start()
    g.word, g.key = "caju", "caju"
    for letter in "bdefgh":
        t = g.play(letter)
    assert t.over and t.winner == "akemi" and "caju" in t.reply


# ------------------------------------------------------------ adivinhação
def test_number_in():
    assert number_in("acho que é 37") == 37
    assert number_in("trinta e sete") == 37
    assert number_in("cinquenta") == 50
    assert number_in("sei lá") is None


def test_guess_number():
    g = GuessNumber(random.Random(0))
    g.start()
    g.secret = 42
    assert g.play("50").reply.startswith("Mais baixo.")
    assert "quente" in g.play("40").reply
    assert g.play("500").reply.startswith("É de 1 a 100")
    t = g.play("quarenta e dois")
    assert t.over and t.winner == "dono" and "em 3 tentativas" in t.reply


# ----------------------------------------------------------------- arcade
@pytest.mark.parametrize("text, game", [
    ("vamos jogar jogo da velha", "velha"), ("bora jogar forca", "forca"),
    ("joga pedra papel e tesoura comigo", "pedra"), ("quero jogar adivinhação", "adivinhacao"),
])
def test_which_game(text, game):
    assert which_game(text) == game


@pytest.mark.asyncio
async def test_arcade_flow():
    boards, results = [], []
    arcade = Arcade(on_board=boards.append, on_result=lambda t, w: results.append((t, w)), rng=random.Random(2))
    assert await arcade.shortcut("5", OWNER) is None  # sem jogo aberto, "5" não é comigo
    r = await arcade.shortcut("Akemi, vamos jogar forca", OWNER)
    assert r.text.startswith("Forca!") and not r.remember and boards
    arcade.game.word, arcade.game.key = "caju", "caju"
    assert await arcade.shortcut("que fome", OWNER) is None  # conversa: o modelo responde
    assert "forca" in arcade.describe_for_prompt()
    for letter in "caj":
        await arcade.shortcut(letter, OWNER)
    r = await arcade.shortcut("u", OWNER)
    assert "Ganhou" in r.text and arcade.game is None and results == [("forca", "dono")]
    r = await arcade.shortcut("de novo", OWNER)
    assert r.text.startswith("Forca!")
    r = await arcade.shortcut("desisto", OWNER)
    assert r.text.startswith("Beleza, parei a partida de forca") and arcade.game is None
    # só o dono joga
    assert await arcade.shortcut("vamos jogar forca", Event("chat.message", trust=Trust.PUBLIC)) is None


@pytest.mark.asyncio
async def test_she_plays_through_the_mind_without_the_model(tmp_path):
    from akemi.brain.providers import EchoProvider
    from akemi.core.config import Settings
    from akemi.core.orchestrator import Akemi
    from akemi.tts.base import ConsoleTTS

    said, boards = [], []
    llm = EchoProvider(lambda m, j: "o modelo não devia falar aqui")
    settings = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0,
                        self_improve_interval_s=0)
    akemi = Akemi(settings, fast=llm, deep=llm, tts=ConsoleTTS(printer=said.append), persist=False,
                  desktop_actions=False)
    akemi.bus.subscribe("jogo.tabuleiro", lambda e: boards.append(e.text))
    await akemi.start()
    try:
        akemi.owner_say("vamos jogar jogo da velha")
        await akemi.bus.drain()
        akemi.owner_say("5")
        await akemi.bus.drain()
        assert len(said) == 2 and said[0].startswith("🗣️  Akemi: Jogo da velha!")
        assert len(boards) == 2 and "X" in boards[1]
        assert not any("modelo" in s for s in said)
        assert akemi.memory.recent(10, kind="conversa") == []  # jogada não vai para a memória
    finally:
        await akemi.stop()
