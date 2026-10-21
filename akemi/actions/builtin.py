"""Ações padrão. Adicione as suas com ``registry.register(...)``."""

from __future__ import annotations

import datetime as dt
import webbrowser
from urllib.parse import urlparse

from akemi.brain.state import InnerState
from akemi.storage.memory import Memory

from .registry import ActionRegistry


def register_builtin_actions(
    registry: ActionRegistry,
    memory: Memory,
    state: InnerState,
) -> None:
    @registry.register("guardar_fato", "guarda na memória um fato duradouro sobre o dono ou o mundo (não é lembrete)",
                       params={"texto": "o fato"},
                       example=("lembra que eu gosto de café sem açúcar",
                                '[[acao:guardar_fato {"texto": "o dono gosta de café sem açúcar"}]]'))
    def guardar_fato(texto: str) -> str:
        return "anotado" if memory.add_fact(texto) else "já sabia"

    @registry.register("esquecer", "apaga fatos guardados que contenham um trecho",
                       params={"trecho": "parte do texto"})
    def esquecer(trecho: str) -> str:
        return f"{memory.forget_fact(trecho)} fato(s) apagado(s)"

    @registry.register("criar_tarefa", "anota uma tarefa para você acompanhar em segundo plano",
                       params={"descricao": "o que fazer"})
    def criar_tarefa(descricao: str) -> str:
        return f"tarefa #{state.add_task(descricao).id} criada"

    @registry.register("concluir_tarefa", "marca uma tarefa como feita", params={"id": "número"})
    def concluir_tarefa(id: int) -> str:
        return "ok" if state.complete_task(int(id)) else "não achei essa tarefa"

    @registry.register("mudar_objetivo", "muda seu objetivo atual", params={"objetivo": "novo objetivo"})
    def mudar_objetivo(objetivo: str) -> str:
        state.current_goal = objetivo.strip()
        state.touch()
        return "ok"

    @registry.register("horario", "diz data e hora atuais", speak_result=True)
    def horario() -> str:
        return dt.datetime.now().strftime("%d/%m/%Y %H:%M")

    @registry.register("abrir_site", "abre um site no navegador",
                       params={"url": "endereço http(s)"})
    def abrir_site(url: str) -> str:
        if "://" not in url:
            url = "https://" + url
        if urlparse(url).scheme not in ("http", "https"):
            raise ValueError("só http/https")
        webbrowser.open(url)
        return "aberto"
