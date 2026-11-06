"""Lembretes: horário lido da frase, salvos em disco, adiar e cancelar."""

import asyncio
import datetime as dt
import json
import time

import pytest

from akemi.actions.registry import ActionCall, ActionRegistry
from akemi.actions.reminders import (
    Reminders,
    parse_reminder,
    register_reminder_actions,
    resolve_when,
    say_when,
)
from akemi.core.events import Trust

NOW = dt.datetime(2026, 11, 6, 14, 0)  # uma sexta, 14h


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
        ("me lembra na próxima sexta de ligar", "13 09:00", "ligar"),
        ("me lembra depois de amanhã às 7 da manhã de acordar cedo", "08 07:00", "acordar cedo"),
        ("me lembra de tomar a segunda dose amanhã", "07 09:00", "tomar a segunda dose"),
        ("põe um timer de 5 minutos", "06 14:05", ""),
        ("timer de meia hora", "06 14:30", ""),
    ],
)
def test_reads_when_and_what(text, due, what):
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
        "me lembra de comprar as 2 coisas que faltam",  # "as 2" é artigo
        "abre o spotify às 10",  # não é pedido de lembrete
    ],
)
def test_not_a_reminder(text):
    assert parse_reminder(text, NOW) is None


def test_today_at_a_time_already_gone_is_not_guessed():
    late = NOW.replace(hour=22)
    assert parse_reminder("me lembra hoje à noite de tirar o lixo", late) is None


@pytest.mark.parametrize(
    "quando,expected",
    [("amanhã às 9h", "07 09:00"), ("10 minutos", "06 14:10"), ("18:30", "06 18:30"),
     ("daqui a 2 horas", "06 16:00"), ("meia hora", "06 14:30"), ("segunda", "09 09:00")],
)
def test_resolve_what_the_model_wrote(quando, expected):
    assert resolve_when(quando, NOW).strftime("%d %H:%M") == expected


def test_says_when_like_a_person():
    n = NOW.timestamp()
    assert say_when(n + 5, n) == "daqui a 5 segundos"
    assert say_when(n + 600, n) == "daqui a 10 minutos"
    assert say_when(n + 2 * 3600, n) == "hoje às 16h"
    assert say_when(dt.datetime(2026, 11, 7, 9, 30).timestamp(), n) == "amanhã às 9h30"
    assert say_when(dt.datetime(2026, 11, 7, 12, 0).timestamp(), n) == "amanhã ao meio-dia"
    assert say_when(dt.datetime(2026, 11, 9, 14, 0).timestamp(), n) == "na segunda às 14h"
    assert say_when(dt.datetime(2026, 11, 8, 13, 0).timestamp(), n) == "depois de amanhã às 13h"
    wednesday = dt.datetime(2026, 11, 4, 14, 0).timestamp()
    assert say_when(dt.datetime(2026, 11, 8, 13, 0).timestamp(), wednesday) == "no domingo às 13h"
    assert say_when(dt.datetime(2026, 11, 20, 8, 0).timestamp(), n) == "dia 20/11 às 8h"


# ------------------------------------------------------------- a agenda
def registry(reminders):
    reg = ActionRegistry()
    register_reminder_actions(reg, reminders)
    return reg


async def ask(reg, name, **args):
    return await reg.request(ActionCall(name, args), Trust.OWNER)


@pytest.mark.asyncio
async def test_reminders_fire_and_cancel():
    fired = []
    reminders = Reminders(lambda r, late: fired.append((r.text, late)))
    reg = registry(reminders)
    r = await ask(reg, "lembrete", minutos="0,002", texto="beber água")
    assert r.status == "executed" and r.value == "Combinado, te lembro daqui a 1 segundo."
    await ask(reg, "lembrete", minutos=30, texto="reunião")
    await asyncio.sleep(0.3)
    assert fired == [("beber água", False)] and [x.text for x in reminders.pending] == ["reunião"]
    r = await ask(reg, "cancelar_lembretes")
    assert r.value == "Cancelei 1 lembrete(s)." and reminders.pending == []
    assert (await ask(reg, "lembrete", minutos="amanhã", texto="x")).status == "error"
    assert (await ask(reg, "lembrete", texto="x")).status == "error"  # sem quando


@pytest.mark.asyncio
async def test_reminder_from_spoken_time():
    reminders = Reminders(lambda r, late: None)
    reg = registry(reminders)
    r = await ask(reg, "lembrete", quando="amanhã às 9h", texto="ligar pro mecânico")
    assert r.value == "Combinado, te lembro amanhã às 9h."
    due = dt.datetime.fromtimestamp(reminders.pending[0].due_at)
    assert (due.date() - dt.date.today()).days == 1 and (due.hour, due.minute) == (9, 0)
    assert (await ask(reg, "lembrete", quando="quando der", texto="x")).status == "error"
    assert (await ask(reg, "lembrete", minutos=60 * 24 * 40, texto="x")).status == "error"  # mais de 30 dias
    reminders.stop()


@pytest.mark.asyncio
async def test_list_and_cancel_one():
    reminders = Reminders(lambda r, late: None)
    reg = registry(reminders)
    assert (await ask(reg, "listar_lembretes")).value == "Nenhum lembrete marcado."
    await ask(reg, "lembrete", minutos=10, texto="tirar a pizza")
    await ask(reg, "lembrete", minutos=20, texto="ligar pro mecânico")
    await ask(reg, "lembrete", minutos=30, texto="beber água")
    listed = (await ask(reg, "listar_lembretes")).value
    assert listed == ("Tenho 3 lembretes: daqui a 10 minutos, tirar a pizza; daqui a 20 minutos, "
                      "ligar pro mecânico; daqui a 30 minutos, beber água.")
    assert reg.get("listar_lembretes").speak_result
    r = await ask(reg, "cancelar_lembrete", qual="o do mecanico")
    assert r.value == "Cancelei o lembrete de ligar pro mecânico."
    assert (await ask(reg, "cancelar_lembrete", qual="último")).value == "Cancelei o lembrete de beber água."
    assert (await ask(reg, "cancelar_lembrete", qual="1")).value == "Cancelei o lembrete de tirar a pizza."
    assert (await ask(reg, "cancelar_lembrete", qual="dentista")).status == "error"


@pytest.mark.asyncio
async def test_saved_and_back_after_restart(tmp_path):
    path = tmp_path / "lembretes.json"
    first = Reminders(lambda r, late: None, path)
    first.add(60, "ligar pro mecânico")
    first.add(120, "pagar o boleto")
    first.cancel("boleto")
    first.stop()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [r["text"] for r in saved["lembretes"]] == ["ligar pro mecânico"]

    again = Reminders(lambda r, late: None, path)
    assert [r.text for r in again.pending] == ["ligar pro mecânico"]
    again.start()
    assert again.pending[0].task is not None
    assert again.add(5, "outro").id == 3  # o número não repete
    again.stop()


@pytest.mark.asyncio
async def test_missed_while_off_fires_late_on_start(tmp_path):
    path = tmp_path / "lembretes.json"
    path.write_text(json.dumps({"proximo_id": 2, "lembretes": [
        {"id": 1, "text": "ligar pro mecânico", "due_at": time.time() - 3600, "created_at": time.time() - 7200},
    ]}), encoding="utf-8")
    fired = []
    reminders = Reminders(lambda r, late: fired.append((r.text, late)), path)
    reminders.start()
    await asyncio.sleep(0.05)
    assert fired == [("ligar pro mecânico", True)]
    assert json.loads(path.read_text(encoding="utf-8"))["lembretes"] == []


def test_broken_file_starts_empty(tmp_path):
    path = tmp_path / "lembretes.json"
    path.write_text("{isso não é json", encoding="utf-8")
    assert Reminders(lambda r, late: None, path).pending == []


@pytest.mark.asyncio
async def test_snooze_right_after_it_rang():
    reminders = Reminders(lambda r, late: None)
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
    reminders.stop()


def test_direct_request_builds_the_action():
    reminders = Reminders(lambda r, late: None)
    call = reminders.direct_request("me lembra amanhã às 9 de ligar pro mecânico", NOW)
    assert call.name == "lembrete"
    assert call.args == {"texto": "ligar pro mecânico", "timestamp": dt.datetime(2026, 11, 7, 9, 0).timestamp()}
    assert reminders.direct_request("lembra que eu gosto de café", NOW) is None


# ---------------------------------------------------------- na Akemi
class Rec:
    def __init__(self):
        self.said = []


def akemi_with(tmp_path, responder, persist=False):
    from akemi.brain.providers import EchoProvider
    from akemi.core.config import Settings
    from akemi.core.orchestrator import Akemi
    from akemi.tts.base import TTSEngine

    rec = Rec()

    class Tts(TTSEngine):
        async def speak(self, text):
            rec.said.append(text)

    settings = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path, idle_think_interval_s=0,
                        self_improve_interval_s=0)
    llm = EchoProvider(responder)
    akemi = Akemi(settings, fast=llm, deep=llm, tts=Tts(), persist=persist, desktop_actions=False)
    return akemi, rec


@pytest.mark.asyncio
async def test_reminder_is_spoken(tmp_path):
    akemi, tts = akemi_with(tmp_path, lambda m, j: '[[acao:lembrete {"minutos": 0.002, "texto": "beber água"}]]')
    assert akemi.actions.get("abrir_programa") is None  # sem ações de PC quando desligadas
    await akemi.start()
    akemi.owner_say("me lembra de beber água")
    await akemi.bus.drain()
    await asyncio.sleep(0.5)
    await akemi.stop()
    assert tts.said == ["Combinado, te lembro daqui a 1 segundo.", "Ei, Mateus, lembrete: beber água"]


@pytest.mark.asyncio
async def test_spoken_reminder_skips_the_model(tmp_path):
    asked = []

    def responder(msgs, json_mode):
        asked.append(msgs)
        return "não devia responder"

    akemi, tts = akemi_with(tmp_path, responder)
    await akemi.start()
    akemi.owner_say("Akemi, põe um timer de 1 segundo")
    await akemi.bus.drain()
    await asyncio.sleep(1.4)
    await akemi.stop()
    assert tts.said == ["Combinado, te lembro daqui a 1 segundo.", "Ei, Mateus, deu o tempo!"]
    assert asked == []


@pytest.mark.asyncio
async def test_missed_reminder_is_told_when_she_is_back(tmp_path):
    due = time.time() - 600
    (tmp_path / "lembretes.json").write_text(json.dumps({"lembretes": [
        {"id": 1, "text": "tirar a roupa da máquina", "due_at": due},
    ]}), encoding="utf-8")
    akemi, tts = akemi_with(tmp_path, lambda m, j: "Oi!", persist=True)
    await akemi.start()
    await asyncio.sleep(0.1)
    await akemi.bus.drain()
    await asyncio.sleep(0.1)
    await akemi.stop()
    assert tts.said == [
        f"Ei, Mateus, passou a hora de um lembrete enquanto eu tava fora. Era {say_when(due)}: tirar a roupa da máquina"
    ]
