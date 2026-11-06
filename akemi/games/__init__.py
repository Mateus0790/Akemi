"""Minijogos por voz: jogo da velha, pedra-papel-tesoura, forca e adivinhação.

Ideia dos minijogos do Capri, feitos para a Akemi jogar por voz ou pelo console
(sem janela). Enquanto um jogo está aberto, as suas jogadas ("5", "letra a",
"pedra") vão direto para o jogo, sem gastar o modelo. Só você joga: o chat da
live, a tela e visitas não mexem no jogo.
"""

from .arcade import Arcade, register_game_actions

__all__ = ["Arcade", "register_game_actions"]
