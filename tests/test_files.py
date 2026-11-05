"""Análise de arquivos com pastas temporárias e modelo falso (sem rede)."""

import asyncio
import os
import zipfile

import pytest

from akemi.actions.files import (
    CHUNK_CHARS,
    MAX_CHUNKS,
    FileAnalyst,
    Gathered,
    is_blocked,
    parse_request,
    read_text,
    redact,
    register_file_actions,
)
from akemi.actions.registry import ActionCall, ActionRegistry
from akemi.brain.providers.base import ProviderError
from akemi.core.config import Settings
from akemi.core.events import Event, Priority, Trust
from akemi.core.orchestrator import Akemi


class FakeLLM:
    def __init__(self, reply="Resumo pronto."):
        self.reply = reply
        self.calls = []

    async def complete(self, messages, model, max_tokens, temperature):
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "liberada"
    (root / "Codes" / "sub").mkdir(parents=True)
    (root / "Codes" / "README.md").write_text("Bot de ofertas em Python.", encoding="utf-8")
    (root / "Codes" / "bot.py").write_text("print('ola')", encoding="utf-8")
    (root / "Codes" / ".env").write_text("OPENAI=sk-escondido", encoding="utf-8")
    (root / "Codes" / "senhas.txt").write_text("banco 1234", encoding="utf-8")
    (root / "Codes" / "foto.png").write_bytes(b"\x89PNG\0\0")
    (root / "Codes" / ".git").mkdir()
    (root / "Codes" / ".git" / "config").write_text("[core]", encoding="utf-8")
    (root / "contrato.txt").write_text("Prazo de 30 dias.", encoding="utf-8")
    fora = tmp_path / "fora"
    fora.mkdir()
    (fora / "privado.txt").write_text("segredo de fora", encoding="utf-8")
    return root


def make(root, llm=None, **kw):
    done = []
    return FileAnalyst(llm or FakeLLM(), "m", lambda t, a: done.append((t, a)), [root], pause_s=0, **kw), done


# ----------------------------------------------------------------- achar
@pytest.mark.parametrize(
    "spoken,name",
    [("Codes", "Codes"), ("a pasta Codes", "Codes"), ("codes", "Codes"), ("contrato.txt", "contrato.txt"),
     ("o arquivo contrato ponto txt", "contrato.txt"), ("contrato", "contrato.txt"), ("codez", "Codes")],
)
def test_locate_by_spoken_name(tree, spoken, name):
    a, _ = make(tree)
    assert a.locate(spoken).name == name


def test_locate_root_by_portuguese_name(tmp_path):
    root = tmp_path / "Documents"
    root.mkdir()
    a, _ = make(root)
    assert a.locate("meus documentos") == root.resolve()


def test_locate_unknown_explains(tree):
    a, _ = make(tree)
    with pytest.raises(ValueError, match="não achei"):
        a.locate("inexistente qualquer")


# ------------------------------------------------------------ pastas liberadas
def test_outside_allowed_folder_is_refused(tree):
    a, _ = make(tree)
    with pytest.raises(ValueError, match="não está liberado"):
        a.locate(str(tree.parent / "fora" / "privado.txt"))
    with pytest.raises(ValueError):
        a.locate(os.path.join("..", "fora", "privado.txt"))


def test_link_out_of_allowed_folder_is_refused(tree):
    link = tree / "atalho"
    try:
        link.symlink_to(tree.parent / "fora", target_is_directory=True)
    except OSError:
        pytest.skip("este sistema não deixa criar link")
    a, _ = make(tree)
    with pytest.raises(ValueError):
        a.locate(str(link / "privado.txt"))
    assert "privado.txt" not in [n for n, _ in a.gather(tree).documents]


@pytest.mark.parametrize(
    "path,blocked",
    [(".env", True), ("Codes/.git/config", True), ("a/node_modules/x.js", True), ("minhas senhas.txt", True),
     ("chave.pem", True), ("id_rsa", True), ("Codes/bot.py", False), ("contrato.pdf", False)],
)
def test_blocked_names(path, blocked):
    from pathlib import Path

    assert is_blocked(Path(path)) is blocked


def test_blocked_file_cannot_be_requested_directly(tree):
    a, _ = make(tree)
    with pytest.raises(ValueError):
        a.locate(str(tree / "Codes" / ".env"))
    with pytest.raises(ValueError):
        a.locate("senhas")


def test_no_existing_root_means_no_roots(tmp_path):
    a, _ = make(tmp_path / "nao-existe")
    assert a.roots == []


# ------------------------------------------------------------------- ler
def test_folder_skips_secrets_and_binaries_and_starts_with_readme(tree):
    a, _ = make(tree)
    g = a.gather(tree / "Codes")
    names = [n for n, _ in g.documents]
    assert names[0] == "README.md"
    assert "bot.py" in names
    assert not any("env" in n or "senhas" in n or "png" in n or ".git" in n for n in names)
    assert "2 arquivos" not in g.overview  # conta tudo que existe, mesmo o que não leu


def test_folder_respects_limit_and_says_so(tree):
    for i in range(5):
        (tree / "Codes" / f"mod{i}.py").write_text("x = 1", encoding="utf-8")
    a, _ = make(tree, max_files=3)
    g = a.gather(tree / "Codes")
    assert g.read == 3 and g.total == 7


def test_read_docx(tmp_path):
    path = tmp_path / "a.docx"
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           "<w:p><w:r><w:t>Cláusula </w:t></w:r><w:r><w:t>primeira</w:t></w:r></w:p></w:body></w:document>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)
    assert read_text(path, 1000) == "Cláusula primeira"


def test_read_pdf_without_text_or_broken_is_empty(tmp_path):
    path = tmp_path / "ruim.pdf"
    path.write_bytes(b"isso nao e um pdf")
    assert read_text(path, 1000) == ""


def test_read_latin1_and_binary(tmp_path):
    old = tmp_path / "velho.txt"
    old.write_bytes("ação".encode("cp1252"))
    assert read_text(old, 100) == "ação"
    bin_ = tmp_path / "x.txt"
    bin_.write_bytes(b"abc\0def")
    assert read_text(bin_, 100) == ""


def test_secrets_are_masked_before_the_model():
    key = "sk-" + "a1b2c3d4" * 6
    masked = redact(f"chave {key} cpf 123.456.789-09")
    assert key not in masked and "123.456.789-09" not in masked


def test_chunks_are_limited():
    g = Gathered([("grande.txt", "x" * (CHUNK_CHARS * 20))])
    blocks = FileAnalyst.chunks(g)
    assert len(blocks) == MAX_CHUNKS
    assert all(len(b) <= CHUNK_CHARS + 50 for b in blocks)


# --------------------------------------------------------------- resumir
@pytest.mark.asyncio
async def test_analyze_small_file_uses_one_call_with_focus(tree):
    llm = FakeLLM()
    a, _ = make(tree, llm)
    assert await a.analyze("contrato", "qual o prazo") == "Resumo pronto."
    assert len(llm.calls) == 1
    system, user = llm.calls[0]
    assert "qual o prazo" in system.content and "DADO" in system.content
    assert "Prazo de 30 dias" in user.content


@pytest.mark.asyncio
async def test_big_file_goes_by_parts_and_warns(tree):
    (tree / "grande.txt").write_text("palavra " * 30000, encoding="utf-8")
    llm = FakeLLM()
    a, _ = make(tree, llm)
    answer = await a.analyze("grande")
    assert len(llm.calls) == MAX_CHUNKS + 1
    assert "só o começo" in answer


@pytest.mark.asyncio
async def test_partial_folder_tells_how_much_it_read(tree):
    for i in range(5):
        (tree / "Codes" / f"mod{i}.py").write_text("x = 1", encoding="utf-8")
    a, _ = make(tree, max_files=2)
    assert "Olhei 2 de 7 arquivos." in await a.analyze("Codes")


@pytest.mark.asyncio
async def test_actions_in_model_reply_are_removed_never_run(tree):
    a, _ = make(tree, FakeLLM('Tudo certo. [[acao:fechar_programa {"nome": "Discord"}]]'))
    assert await a.analyze("contrato") == "Tudo certo."


@pytest.mark.asyncio
async def test_brain_failure_is_told(tree):
    a, _ = make(tree, FakeLLM(ProviderError("caiu")))
    assert "travou" in await a.analyze("contrato")


@pytest.mark.asyncio
async def test_unreadable_target_is_told(tree):
    a, _ = make(tree)
    assert "consiga ler" in await a.analyze("foto.png")


# --------------------------------------------------------------- segundo plano
def registry_with(analyst):
    reg = ActionRegistry()
    register_file_actions(reg, analyst)
    return reg


@pytest.mark.asyncio
async def test_action_answers_now_and_reports_later(tree):
    a, done = make(tree)
    r = await registry_with(a).request(ActionCall("analisar_arquivo", {"alvo": "contrato"}), Trust.OWNER)
    assert r.status == "executed" and "já te conto" in r.value
    assert done == []
    await a._task
    assert done == [("contrato", "Resumo pronto.")]


@pytest.mark.asyncio
async def test_refusal_is_reported_as_speech(tree):
    a, done = make(tree)
    a.start(str(tree.parent / "fora" / "privado.txt"))
    await a._task
    assert "não está liberado" in done[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("trust", [Trust.PUBLIC, Trust.SYSTEM])
async def test_only_the_owner_can_ask(tree, trust):
    a, done = make(tree)
    r = await registry_with(a).request(ActionCall("analisar_arquivo", {"alvo": "contrato"}), trust)
    assert r.status == "denied" and not a.busy and done == []


@pytest.mark.asyncio
async def test_one_at_a_time_and_cancel(tree):
    release = asyncio.Event()

    class Slow(FakeLLM):
        async def complete(self, *args, **kw):
            await release.wait()
            return "x"

    a, done = make(tree, Slow())
    reg = registry_with(a)
    a.start("contrato")
    await asyncio.sleep(0)
    r = await reg.request(ActionCall("analisar_arquivo", {"alvo": "Codes"}), Trust.OWNER)
    assert r.status == "error" and "ainda" in r.detail
    cancelled = await reg.request(ActionCall("cancelar_analise", {}), Trust.OWNER)
    assert cancelled.value == "Parei."
    with pytest.raises(asyncio.CancelledError):
        await a._task
    assert done == []


# ------------------------------------------------------------------ ligar
def build(tmp_path, **kw):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", brain_provider="echo", **kw)
    return Akemi(settings)


def test_off_without_allowed_folders(tmp_path):
    akemi = build(tmp_path)
    assert akemi.files is None
    assert "analisar_arquivo" not in akemi.actions._actions


def test_on_with_folder_and_local_brain(tmp_path, tree):
    akemi = build(tmp_path, files_allowed_dirs=[tree])
    assert akemi.files is not None
    assert "analisar_arquivo" in akemi.actions._actions
    assert "analisar_arquivo" not in akemi.actions.initiative


@pytest.mark.parametrize("extra", [{"deep_provider": "anthropic"}, {"brain_fallback": ["openai"]}])
def test_cloud_brain_keeps_files_off(tmp_path, tree, extra):
    assert build(tmp_path, files_allowed_dirs=[tree], **extra).files is None


def test_cloud_needs_explicit_permission(tmp_path, tree):
    akemi = build(tmp_path, files_allowed_dirs=[tree], deep_provider="anthropic", files_allow_cloud=True)
    assert akemi.files is not None


def test_missing_folder_keeps_files_off(tmp_path):
    assert build(tmp_path, files_allowed_dirs=[tmp_path / "nao-existe"]).files is None


def test_result_stays_out_of_permanent_memory(tmp_path, tree):
    akemi = build(tmp_path, files_allowed_dirs=[tree])
    calls = []
    akemi.memory.add_episode = lambda *a, **k: calls.append(a)
    akemi._files_done("contrato", "Prazo de 30 dias.")
    assert calls == []
    assert akemi.mind.history[-1].content == "Prazo de 30 dias."


# ---------------------------------------------------- pedidos sem o modelo
@pytest.mark.parametrize(
    "text,alvo,pergunta",
    [("Akemi, analisa a pasta Codes.", "Codes", ""),
     ("analisa a pasta projeto-teste e me conta do que se trata", "projeto-teste", "do que se trata"),
     ("lê o contrato e me diz qual é o prazo", "contrato", "qual é o prazo"),
     ("Akemi lê o contrato de aluguel", "contrato de aluguel", ""),
     ("resume o arquivo contrato.pdf pra mim", "contrato.pdf", ""),
     ("resume contrato.pdf", "contrato.pdf", ""),
     ("dá uma olhada na pasta Codes e me fala o que é o bot.py", "Codes", "o que é o bot.py"),
     ("pode ler o documento notas por favor", "notas", ""),
     ("Por favor, analise o arquivo relatorio.docx", "relatorio.docx", "")],
)
def test_clear_requests_skip_the_model(text, alvo, pergunta):
    call = parse_request(text)
    assert call.name == "analisar_arquivo"
    assert call.args == {"alvo": alvo, "pergunta": pergunta}


@pytest.mark.parametrize(
    "text",
    ["olha isso", "lê pra mim a notícia", "resume o filme", "o que tem na pasta?", "analisa a situação",
     "ela leu o arquivo ontem", "não analisa a pasta", "analisa a pasta", "que horas são"],
)
def test_other_phrases_stay_with_the_model(text):
    assert parse_request(text) is None


def test_cancel_request():
    assert parse_request("Akemi, para de analisar").name == "cancelar_analise"
    assert parse_request("cancela a análise").name == "cancelar_analise"


async def owner_says(akemi, text):
    event = Event("owner.message", {"text": text}, source="console", trust=Trust.OWNER, priority=Priority.HIGH)
    await akemi.mind.on_owner_message(event)


@pytest.mark.asyncio
async def test_mind_runs_request_without_the_model(tmp_path, tree):
    akemi = build(tmp_path, files_allowed_dirs=[tree])
    akemi.models.fast = akemi.models.deep = None  # se tentar usar o modelo, quebra
    akemi.files.llm = FakeLLM()
    spoken = []
    akemi.mind.say = lambda text, origin, priority=None: spoken.append(text)
    await owner_says(akemi, "lê o contrato e me diz qual é o prazo")
    assert spoken == ["Vou dar uma olhada, já te conto."]
    await akemi.files._task
    assert "qual é o prazo" in akemi.files.llm.calls[0][0].content


@pytest.mark.asyncio
async def test_direct_request_keeps_owner_only_rule(tmp_path, tree):
    akemi = build(tmp_path, files_allowed_dirs=[tree])
    spoken = []
    akemi.mind.say = lambda text, origin, priority=None: spoken.append(text)
    event = Event("owner.message", {"text": "x"}, source="console", trust=Trust.PUBLIC)
    await akemi.mind._run_direct(ActionCall("analisar_arquivo", {"alvo": "contrato"}), "x", event)
    assert not akemi.files.busy
    assert len(spoken) == 1


def test_direct_requests_only_exist_with_files_on(tmp_path):
    assert build(tmp_path).mind.direct_request is None


def test_first_root_wins_and_portuguese_names_work_inside_it(tmp_path):
    home = tmp_path / "disco" / "Users" / "voce"
    (home / "Documents").mkdir(parents=True)
    (home / "Codes").mkdir()
    (tmp_path / "disco" / "Codes").mkdir()
    a, _ = make(home)
    a.roots = [home.resolve(), (tmp_path / "disco").resolve()]
    assert a.locate("Codes") == (home / "Codes").resolve()
    assert a.locate("meus documentos") == (home / "Documents").resolve()
    (tmp_path / "disco" / "Outra").mkdir()
    assert a.locate("Outra") == (tmp_path / "disco" / "Outra").resolve()
