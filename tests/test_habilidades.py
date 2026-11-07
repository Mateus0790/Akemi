"""Habilidades: ela percebe o programa aberto e cria um jeito de participar."""

import asyncio

import pytest

from akemi.actions.registry import ActionCall, ActionRegistry, Tier
from akemi.brain.providers import EchoProvider
from akemi.core.events import Event, Trust
from akemi.habilidades import ActivityWatcher, Habilidades, interesting, register_skill_actions
from akemi.habilidades.atividade import Program, Window, from_phrase, from_process, parse_powershell
from akemi.habilidades.construtor import Builder, parse_files
from akemi.habilidades.limites import blocked
from akemi.habilidades.plugin import check_code

GOOD = '''=== habilidade.yaml
id: stardew
nome: Stardew Valley
programa: stardew
descricao: acompanha o Stardew
ponte: saves
=== habilidade.py
import asyncio


def ligar(akemi):
    estado = {"aberto": False}

    @akemi.ao_abrir
    def abriu():
        estado["aberto"] = True
        akemi.perceber("o dono abriu o Stardew")

    @akemi.ao_fechar
    def fechou():
        estado["aberto"] = False

    @akemi.tarefa
    async def rotina():
        while True:
            await asyncio.sleep(3600)

    def status():
        return "Tô no Stardew." if estado["aberto"] else "O Stardew tá fechado."

    akemi.acao("status", "diz se ela está no Stardew", status)
    akemi.acao("dormir", "manda o personagem dormir", lambda: "ok", confirmar="Mando dormir?")
=== FIM'''

EVIL = GOOD.replace("import asyncio\n", "import asyncio\nimport subprocess\n", 1)
NOTHING = GOOD.split("=== habilidade.py")[0] + "=== habilidade.py\ndef ligar(akemi):\n    pass\n=== FIM"


# ------------------------------------------------------------------- limites
@pytest.mark.parametrize("name", ["VALORANT-Win64-Shipping.exe", "cs2", "FortniteClient-Win64-Shipping",
                                  "r5apex", "EasyAntiCheat_EOS", "League of Legends", "league-of-legends",
                                  "KeePassXC", "Bitwarden", "python", "SomethingAntiCheat"])
def test_anticheat_and_sensitive_programs_are_off_limits(name):
    assert blocked(name)


def test_owner_can_block_more_and_normal_programs_pass():
    assert blocked("minecraft") is None and blocked("Stardew Valley") is None
    assert blocked("chrome", ["Chrome.exe"]) == "você bloqueou esse programa"


def test_code_guard_refuses_what_a_skill_never_needs():
    _, code = parse_files(GOOD)
    assert check_code(code) == []
    for bad in ("import subprocess", "from akemi.safety import moderation", "import os\nos.system('x')",
                "eval('1')", "x = ().__class__", "import os\nos.environ['A']", "import shutil",
                "import os\nos.remove('a')"):
        assert check_code(bad + "\n\ndef ligar(akemi):\n    pass\n"), bad
    assert check_code("x = 1\n") == ["falta a função ligar(akemi)"]
    assert check_code("lista = [1]\nlista.remove(1)\n'a'.replace('a', 'b')\n\ndef ligar(akemi):\n    pass\n") == []


# ------------------------------------------------------------------ atividade
@pytest.mark.parametrize("text, key", [
    ("vou jogar minecraft", "minecraft"), ("Akemi, vou jogar mine agora", "minecraft"),
    ("bora jogar stardew valley", "stardew"), ("vou abrir o OBS", "obs"),
    ("vou usar o Blender um pouco", "blender"), ("vou jogar Hollow Knight", "hollow-knight"),
])
def test_understands_what_he_is_going_to_do(text, key):
    assert from_phrase(text).key == key


def test_ignores_phrases_that_are_not_about_a_program():
    assert from_phrase("que horas são?") is None and from_phrase("gosto de minecraft") is None


def test_processes_map_to_programs():
    assert from_process("Minecraft.Windows").key == "minecraft"
    assert from_process("javaw", title="Minecraft 1.21.1").key == "minecraft"
    assert from_process("javaw", title="Eclipse").key == "javaw"
    assert from_process("Stardew Valley").key == "stardew"
    wins = parse_powershell('[{"ProcessName":"obs64","Path":"C:\\\\OBS\\\\obs64.exe","MainWindowTitle":"OBS"}]')
    assert wins[0].process == "obs64" and wins[0].exe.endswith("obs64.exe")
    assert parse_powershell('{"ProcessName":"x","Path":null,"MainWindowTitle":""}')[0].process == "x"
    assert parse_powershell("lixo") == []


def test_only_asks_by_itself_about_games_and_known_programs():
    assert interesting(Program("obs", "OBS"))
    assert interesting(Program("hades", "Hades", exe="D:\\SteamLibrary\\steamapps\\common\\Hades\\Hades.exe"))
    assert not interesting(Program("notepad", "Notepad", exe="C:\\Windows\\notepad.exe"))


@pytest.mark.asyncio
async def test_watcher_notices_open_and_close():
    snapshots = iter([[Window("obs64")], [Window("obs64"), Window("Stardew Valley")], [Window("Stardew Valley")]])
    events = []

    async def lister():
        return next(snapshots)

    w = ActivityWatcher(lambda p: events.append(("abriu", p.key)), lambda p: events.append(("fechou", p.key)),
                        lister=lister)
    for _ in range(3):
        await w.step()
    assert events == [("abriu", "obs"), ("abriu", "stardew"), ("fechou", "obs")]


# ---------------------------------------------------------------- construtor
def builder(tmp_path, replies):
    it = iter(replies)
    calls = []

    def responder(messages, json_mode):
        calls.append(list(messages))
        return next(it)

    return Builder(EchoProvider(responder), "m", tmp_path / "habilidades"), calls


@pytest.mark.asyncio
async def test_builds_a_draft_that_passes_the_test(tmp_path):
    b, calls = builder(tmp_path, [GOOD])
    result = await b.build(Program("stardew", "Stardew Valley", ("saves",)))
    assert result.status == "rascunho", result.detail
    assert "status" in result.summary and "rotina" in result.summary
    assert (tmp_path / "habilidades" / "stardew" / "habilidade.py").exists()
    prompt = calls[0][-1].content
    assert "Stardew Valley" in prompt and "saves" in prompt


@pytest.mark.asyncio
async def test_reads_the_refusal_and_fixes_it(tmp_path):
    b, calls = builder(tmp_path, [EVIL, NOTHING, GOOD])
    result = await b.build(Program("stardew", "Stardew Valley"))
    assert result.status == "rascunho"
    assert "subprocess" in calls[1][-1].content and "não registrou nada" in calls[2][-1].content


@pytest.mark.asyncio
async def test_gives_up_and_saves_nothing(tmp_path):
    b, _ = builder(tmp_path, [EVIL] * 3)
    result = await b.build(Program("stardew", "Stardew Valley"))
    assert result.status == "falhou" and not (tmp_path / "habilidades" / "stardew").exists()


@pytest.mark.asyncio
async def test_refuses_code_with_secrets(tmp_path):
    leaked = GOOD.replace('"Tô no Stardew."', '"nvapi-' + "a" * 40 + '"')
    b, _ = builder(tmp_path, [leaked])
    assert (await b.build(Program("stardew", "Stardew Valley"))).status == "recusado"


# ------------------------------------------------------------------- gerente
def make_skills(tmp_path, replies=(GOOD,), **kw):
    registry = ActionRegistry()
    said, seen, asked = [], [], []
    b, _ = builder(tmp_path, list(replies))

    def ask(call, question):
        result = registry.propose(call, {"criar_habilidade"})
        asked.append(question)
        return result.status == "pending_confirmation"

    skills = Habilidades(tmp_path / "habilidades", registry, said.append, lambda k, t: seen.append((k, t)),
                         builder=b, ask=ask, **kw)
    register_skill_actions(registry, skills)
    return skills, registry, said, seen, asked


async def finish(skills):
    await asyncio.gather(*skills.tasks)


@pytest.mark.asyncio
async def test_full_flow_asks_builds_waits_for_review_then_is_there(tmp_path):
    skills, registry, said, seen, asked = make_skills(tmp_path)
    stardew = Program("stardew", "Stardew Valley")
    await skills.on_open(stardew)
    assert "Stardew Valley" in asked[0] and registry.pending is not None
    await skills.on_open(stardew)  # não pergunta de novo
    assert len(asked) == 1

    result = await registry.resolve_confirmation(True, Trust.OWNER)
    assert result.status == "executed" and "segundo plano" in result.value
    await finish(skills)
    assert skills.status("stardew") == "rascunho" and "/habilidade ativar stardew" in said[-1]
    assert registry.get("stardew_status") is None  # rascunho não roda

    assert "Liguei" in await skills.activate("stardew")
    assert registry.get("stardew_status") is not None
    assert registry.get("stardew_dormir").tier == Tier.CONFIRM
    assert seen == [("stardew", "o dono abriu o Stardew")]  # o jogo estava aberto: já entrou
    assert skills.plugins["stardew"].tasks
    await skills.on_close(stardew)
    assert not skills.plugins["stardew"].tasks

    # Ela reinicia: a habilidade liga sozinha e está lá quando o jogo abre.
    again, reg2, _, seen2, asked2 = make_skills(tmp_path)
    assert again.load_active() == ["stardew"]
    await again.on_open(stardew)
    assert seen2 == [("stardew", "o dono abriu o Stardew")] and asked2 == []
    await again.stop()

    assert "Desliguei" in await again.deactivate("stardew")
    assert reg2.get("stardew_status") is None
    await skills.stop()


@pytest.mark.asyncio
async def test_never_touches_anticheat_games(tmp_path):
    skills, registry, _, _, asked = make_skills(tmp_path)
    await skills.on_open(Program("valorant", "Valorant"))
    assert asked == [] and registry.pending is None
    with pytest.raises(ValueError, match="anti-cheat"):
        skills.start_build("Valorant")
    reply = await skills.shortcut("vou jogar valorant", Event("owner.message", trust=Trust.OWNER))
    assert reply is None


@pytest.mark.asyncio
async def test_saying_no_means_no_unprompted_asking_again(tmp_path):
    skills, registry, _, _, asked = make_skills(tmp_path)
    await skills.on_open(Program("obs", "OBS"))
    await registry.resolve_confirmation(False, Trust.OWNER)
    await skills.on_close(Program("obs", "OBS"))
    await skills.on_open(Program("obs", "OBS"))
    assert len(asked) == 1 and skills.tasks == []


@pytest.mark.asyncio
async def test_going_to_play_offers_to_build(tmp_path):
    skills, registry, _, _, _ = make_skills(tmp_path)
    reply = await skills.shortcut("vou jogar stardew", Event("owner.message", trust=Trust.OWNER))
    assert reply.startswith("Boa!") and "Stardew Valley" in reply and registry.pending is not None
    # Do chat da live não vale.
    registry._pending = None
    assert await skills.shortcut("vou jogar stardew", Event("chat.batch", trust=Trust.PUBLIC)) is None


@pytest.mark.asyncio
async def test_daily_limit_and_creating_is_never_initiative(tmp_path):
    skills, registry, _, _, _ = make_skills(tmp_path, replies=[GOOD, GOOD], per_day=1)
    skills.start_build("Stardew Valley")
    await finish(skills)
    with pytest.raises(ValueError, match="24 horas"):
        skills.start_build("OBS")
    registry.allow_initiative(["criar_habilidade", "habilidades"])
    assert registry.initiative == {"habilidades"}
    denied = await registry.request(ActionCall("criar_habilidade", {"programa": "OBS"}), Trust.PUBLIC)
    assert denied.status == "denied"


@pytest.mark.asyncio
async def test_broken_or_tampered_plugin_does_not_load(tmp_path):
    skills, registry, _, _, _ = make_skills(tmp_path)
    skills.start_build("Stardew Valley")
    await finish(skills)
    code = tmp_path / "habilidades" / "stardew" / "habilidade.py"
    code.write_text(code.read_text() + "\nimport subprocess\n")
    with pytest.raises(Exception, match="subprocess"):
        await skills.activate("stardew")
    skills._set("stardew", "ativa")
    assert skills.load_active() == [] and skills.status("stardew") == "desativada"


# ------------------------------------------------------------ dentro da Akemi
@pytest.mark.asyncio
async def test_inside_akemi_going_to_play_then_yes_builds_in_background(tmp_path):
    from akemi.core.config import Settings
    from akemi.core.orchestrator import Akemi
    from tests.test_mind_integration import RecordingTTS, settle

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", brain_provider="echo",
                        idle_think_interval_s=0, self_improve_interval_s=0)
    llm = EchoProvider(lambda m, j: GOOD if "habilidade.yaml" in m[-1].content else "Beleza!")
    tts = RecordingTTS()
    akemi = Akemi(settings, fast=llm, deep=llm, tts=tts, desktop_actions=False)
    akemi.skills.builder.llm = llm
    await akemi.start()
    akemi.owner_say("vou jogar stardew")
    await settle(akemi)
    assert "Quer que eu crie um jeito de participar do Stardew Valley" in tts.said[-1]
    akemi.owner_say("sim")
    await settle(akemi)
    await asyncio.gather(*akemi.skills.tasks)
    await settle(akemi)
    await akemi.stop()
    assert any("segundo plano" in s for s in tts.said)
    assert "/habilidade ativar stardew" in tts.said[-1]
    assert akemi.actions.get("stardew_status") is None  # só depois de você ativar
