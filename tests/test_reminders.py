"""Lembretes entendidos direto da frase, adiar e cancelar um (o resto em test_lembretes_vigia.py)."""

import asyncio
from datetime import datetime

import pytest

from akemi.actions.registry import ActionCall, ActionRegistry
from akemi.actions.reminders import TIMER_TEXT, Reminders, parse_reminder, register_reminder_actions
from akemi.actions.when import find_when, parse_when
from akemi.core.events import Trust

NOW = datetime(2026, 11, 6, 14, 0)  # uma sexta, 14h


@pytest.mark.parametrize(
    "text,due,what",
    [
        ("me lembra daqui a 10 minutos de tirar a pizza do forno", "06 14:10", "tirar a pizza do forno"),
        ("Akemi, me lembra de tirar a pizza daqui a dez minutos", "06 14:10", "tirar a pizza"),
        ("pode me lembrar em uma hora e meia de sair?", "06 15:30", "sair"),
        ("me lembra daqui a 1h30 de desligar o forno", "06 15:30", "desligar o forno"),
        ("me lembra em 2 horas e 15 de tirar o bolo", "06 16:15", "tirar o bolo"),
        ("me lembra amanhã às 9 de ligar pro mecânico", "07 09:00", "ligar pro mecânico"),
        ("me lembra amanhã às 3 de ir no médico", "07 15:00", "ir no médico"),
        ("me lembra às 3 de buscar o João", "06 15:00", "buscar o João"),
        ("me lembra às 18h30 de ligar pra mãe, tá?", "06 18:30", "ligar pra mãe"),
        ("cria um lembrete pras 20h de ver o jogo", "06 20:00", "ver o jogo"),
        ("me lembra às nove e meia da noite de tomar o remédio", "06 21:30", "tomar o remédio"),
        ("me lembra hoje à noite de tirar o lixo", "06 20:00", "tirar o lixo"),
        ("me lembra ao meio-dia de almoçar", "07 12:00", "almoçar"),
        ("me lembra na segunda de pagar a conta de luz", "09 09:00", "pagar a conta de luz"),
        ("me lembra sexta às 10 da reunião", "13 10:00", "reunião"),  # hoje é sexta e 10h já foi
        ("me lembra dia 5 de dezembro às 9h de renovar a CNH", "05 09:00", "renovar a CNH"),
        ("me lembra depois de amanhã às 7 da manhã de acordar cedo", "08 07:00", "acordar cedo"),
        ("me lembra de tomar a segunda dose amanhã", "07 09:00", "tomar a segunda dose"),
        ("põe um timer de 5 minutos", "06 14:05", TIMER_TEXT),
        ("timer de 30 segundos", "06 14:00", TIMER_TEXT),
    ],
)
def test_reads_when_and_what_from_the_sentence(text, due, what):
    r = parse_reminder(text, NOW)
    assert r is not None
    assert r.due.strftime("%d %H:%M") == due
    assert r.text == what


@pytest.mark.parametrize(
    "text",
    [
        "lembra que eu gosto de café sem açúcar",  # é fato, não lembrete
        "me lembra o que eu falei ontem às 10",
        "você me lembra minha mãe às vezes",
        "me avisa quando o download terminar",  # sem horário: o modelo pergunta
        "me lembra daqui a 10 minutos",  # de quê? o modelo pergunta
        "me lembra de comprar as 2 coisas que faltam",  # "as 2" é artigo
        "abre o spotify às 10",  # não é pedido de lembrete
    ],
)
def test_not_a_reminder(text):
    assert parse_reminder(text, NOW) is None


def test_bare_hours_are_the_next_one_but_written_hours_are_literal():
    assert parse_when("às 3", NOW) == datetime(2026, 11, 6, 15, 0)  # depois do almoço
    assert parse_when("às 9", NOW) == datetime(2026, 11, 6, 21, 0)
    assert parse_when("às 9h", NOW) == datetime(2026, 11, 7, 9, 0)  # "9h" é 9h
    assert parse_when("10 minutos", NOW) == datetime(2026, 11, 6, 14, 10)  # o modelo esquece o "daqui a"
    sentence = "me lembra de tirar a pizza daqui a dez minutos"
    (start, end), = find_when(sentence, NOW).spans
    assert sentence[start:end] == "daqui a dez minutos"


def registry(reminders):
    reg = ActionRegistry()
    register_reminder_actions(reg, reminders)
    return reg


async def ask(reg, name, **args):
    return await reg.request(ActionCall(name, args), Trust.OWNER)


@pytest.mark.asyncio
async def test_direct_request_runs_as_a_normal_reminder():
    reminders = Reminders(lambda text: None)
    reg = registry(reminders)
    call = reminders.direct_request("me lembra daqui a 10 minutos de tirar a pizza")
    assert call.name == "lembrete" and call.args["texto"] == "tirar a pizza"
    r = await reg.request(call, Trust.OWNER)
    assert r.value == "Combinado, te lembro daqui a 10 minutos."
    assert reminders.direct_request("lembra que eu gosto de café") is None
    reminders.cancel_all()


@pytest.mark.asyncio
async def test_snooze_right_after_it_rang():
    reminders = Reminders(lambda text: None)
    reg = registry(reminders)
    assert reminders.direct_request("adia 15 minutos") is None  # nada tocou ainda
    await ask(reg, "lembrete", minutos=0.001, texto="tirar a pizza")
    await asyncio.sleep(0.2)
    assert reminders.direct_request("adia 15 minutos") == ActionCall("adiar_lembrete", {"minutos": 15.0})
    assert reminders.direct_request("mais meia hora").args == {"minutos": 30.0}
    assert reminders.direct_request("soneca").args == {"minutos": 10}
    r = await reg.request(reminders.direct_request("adia 15 minutos"), Trust.OWNER)
    assert r.value == "Tá, te lembro de novo daqui a 15 minutos."
    assert [x.text for x in reminders.pending] == ["tirar a pizza"]
    assert (await ask(reg, "adiar_lembrete")).status == "error"  # já adiou: não tem outro tocando
    reminders.cancel_all()


@pytest.mark.asyncio
async def test_cancel_one_by_text_or_position():
    reminders = Reminders(lambda text: None)
    reg = registry(reminders)
    for minutes, text in ((10, "tirar a pizza"), (20, "ligar pro mecânico"), (30, "beber água")):
        await ask(reg, "lembrete", minutos=minutes, texto=text)
    assert (await ask(reg, "cancelar_lembrete", qual="o do mecanico")).value == "Cancelei o lembrete de ligar pro mecânico."
    assert (await ask(reg, "cancelar_lembrete", qual="último")).value == "Cancelei o lembrete de beber água."
    assert (await ask(reg, "cancelar_lembrete", qual="1")).value == "Cancelei o lembrete de tirar a pizza."
    assert (await ask(reg, "cancelar_lembrete", qual="dentista")).status == "error"
    assert reminders.pending == []


# ---------------------------------------------------------- na Akemi
@pytest.mark.asyncio
async def test_spoken_reminder_skips_the_model(tmp_path):
    from akemi.brain.providers import EchoProvider
    from akemi.core.config import Settings
    from akemi.core.orchestrator import Akemi
    from akemi.tts.base import TTSEngine

    said, asked = [], []

    class Tts(TTSEngine):
        async def speak(self, text):
            said.append(text)

    def responder(msgs, json_mode):
        asked.append(msgs)
        return "não devia responder"

    settings = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0)
    llm = EchoProvider(responder)
    akemi = Akemi(settings, fast=llm, deep=llm, tts=Tts(), persist=False, desktop_actions=False)
    await akemi.start()
    akemi.owner_say("Akemi, põe um timer de 1 segundo")
    await akemi.bus.drain()
    await asyncio.sleep(1.4)
    await akemi.stop()
    assert said == ["Combinado, te lembro daqui a 1 segundo.", f"Ei, Mateus, lembrete: {TIMER_TEXT}"]
    assert asked == []
