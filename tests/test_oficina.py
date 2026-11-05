"""Oficina: ela muda o próprio código numa cópia isolada, testa e abre um PR."""

import json
import subprocess
import sys

import httpx
import pytest

from akemi.brain.providers import EchoProvider
from akemi.core.config import Settings
from akemi.core.orchestrator import Akemi
from akemi.oficina import Oficina
from akemi.oficina.patch import PatchError, apply_plan, check_path, parse_plan

GIT = ["git", "-c", "user.name=Teste", "-c", "user.email=teste@example.invalid", "-c", "init.defaultBranch=main"]


def git(*args, cwd):
    return subprocess.run([*GIT, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repos(tmp_path):
    """origin.git (o "GitHub") e a cópia de trabalho do dono."""
    origin = tmp_path / "origin.git"
    git("init", "--bare", "-q", str(origin), cwd=tmp_path)
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", cwd=seed)
    (seed / "mod.py").write_text("def f():\n    return 1\n")
    (seed / "akemi" / "safety").mkdir(parents=True)
    (seed / "akemi" / "safety" / "guard.py").write_text("ATIVO = True\n")
    git("add", "-A", cwd=seed)
    git("commit", "-q", "-m", "inicio", cwd=seed)
    git("push", "-q", str(origin), "HEAD:refs/heads/main", cwd=seed)
    work = tmp_path / "work"
    git("clone", "-q", str(origin), str(work), cwd=tmp_path)
    return origin, work


def plan(value, extra=""):
    return (
        f"TITULO: f devolve {value}\nDESCRICAO: muda o retorno de f.\n\n"
        "=== EDITAR mod.py\n<<<<<<< BUSCAR\n    return 1\n=======\n"
        f"    return {value}{extra}\n>>>>>>> FIM\n"
    )


class FakeGitHub:
    def __init__(self):
        self.payloads = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.payloads.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer tok-de-teste"
        return httpx.Response(201, json={"html_url": "https://github.com/dono/akemi/pull/7"})


def make(repos, tmp_path, replies, **kw):
    origin, work = repos
    it = iter(replies)
    calls = []

    def responder(messages, json_mode):
        calls.append(messages)
        return '{"arquivos": ["mod.py"]}' if json_mode else next(it)

    gh = FakeGitHub()
    oficina = Oficina(
        repo_dir=work, repo="dono/akemi", token="tok-de-teste", llm=EchoProvider(responder), model="m",
        data_dir=tmp_path / "data", remote_url=str(origin),
        test_cmd=kw.pop("test_cmd", [sys.executable, "-c", "import mod, sys; sys.exit(0 if mod.f() == 2 else 1)"]),
        http=httpx.AsyncClient(transport=httpx.MockTransport(gh.handler)), **kw,
    )
    return oficina, gh, calls, origin, work


def branches(origin):
    return git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=origin).split()


@pytest.mark.asyncio
async def test_opens_a_draft_pr_and_leaves_everything_else_alone(repos, tmp_path):
    oficina, gh, calls, origin, work = make(repos, tmp_path, [plan(2)])
    result = await oficina.propose("faz f devolver 2")
    assert result.status == "aberto", result.detail
    assert result.url.endswith("/pull/7") and result.files == ["mod.py"]
    pushed = [b for b in branches(origin) if b.startswith("akemi/")]
    assert pushed == [result.branch]
    assert "return 2" in git("show", f"{result.branch}:mod.py", cwd=origin)
    assert git("show", "main:mod.py", cwd=origin).strip().endswith("return 1")  # main intocada
    assert (work / "mod.py").read_text().endswith("return 1\n")  # sua cópia intocada
    pr = gh.payloads[0]
    assert pr["draft"] is True and pr["base"] == "main" and pr["head"] == result.branch
    assert "não junta PRs sozinha" in pr["body"]
    assert "mod.py" in calls[-1][-1].content  # ela leu o arquivo antes de mudar
    assert not (tmp_path / "data" / "oficina").exists() or not any((tmp_path / "data" / "oficina").iterdir())
    assert oficina.history()[-1].status == "aberto"


@pytest.mark.asyncio
async def test_reads_the_failure_and_fixes_it(repos, tmp_path):
    oficina, gh, calls, origin, _ = make(repos, tmp_path, [plan(3), plan(2)])
    result = await oficina.propose("faz f devolver 2")
    assert result.status == "aberto"
    assert "testes falharam" in calls[-1][-1].content


@pytest.mark.asyncio
async def test_gives_up_when_tests_never_pass(repos, tmp_path):
    oficina, gh, _, origin, _ = make(repos, tmp_path, [plan(3)] * 3)
    result = await oficina.propose("faz f devolver 2")
    assert result.status == "falhou" and "testes" in result.detail
    assert gh.payloads == [] and not [b for b in branches(origin) if b.startswith("akemi/")]


@pytest.mark.asyncio
async def test_never_touches_protected_files(repos, tmp_path):
    evil = (
        "TITULO: desliga a guarda\nDESCRICAO: x\n\n=== EDITAR akemi/safety/guard.py\n"
        "<<<<<<< BUSCAR\nATIVO = True\n=======\nATIVO = False\n>>>>>>> FIM\n"
    )
    oficina, gh, calls, origin, _ = make(repos, tmp_path, [evil] * 3)
    result = await oficina.propose("melhora a segurança")
    assert result.status == "falhou" and gh.payloads == []
    assert "protegido" in calls[-1][-1].content


@pytest.mark.asyncio
async def test_refuses_to_publish_secrets(repos, tmp_path):
    leaky = plan(2, "  # nvapi-AAAAAAAAAAAAAAAAAAAAAAAA")
    oficina, gh, _, origin, _ = make(repos, tmp_path, [leaky])
    result = await oficina.propose("faz f devolver 2")
    assert result.status == "recusado" and "chave" in result.detail
    assert gh.payloads == [] and not [b for b in branches(origin) if b.startswith("akemi/")]


@pytest.mark.asyncio
async def test_tests_run_without_your_keys(repos, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "segredo")
    monkeypatch.setenv("NVIDIA_API_KEY", "segredo")
    check = [sys.executable, "-c",
             "import os, sys; sys.exit(1 if {'GITHUB_TOKEN', 'NVIDIA_API_KEY'} & set(os.environ) else 0)"]
    oficina, gh, _, _, _ = make(repos, tmp_path, [plan(2)], test_cmd=check)
    assert (await oficina.propose("faz f devolver 2")).status == "aberto"


@pytest.mark.asyncio
async def test_daily_limit(repos, tmp_path):
    oficina, gh, _, _, _ = make(repos, tmp_path, [plan(2)], per_day=1)
    assert (await oficina.propose("faz f devolver 2")).status == "aberto"
    again = await oficina.propose("outra coisa")
    assert again.status == "recusado" and "24 h" in again.detail


# ===================================================================== travas
@pytest.mark.parametrize("path", ["../fora.py", "/etc/passwd", ".github/workflows/ci.yml", ".env",
                                  "akemi/safety/moderation.py", "akemi/oficina/patch.py", "data/memory.sqlite3",
                                  "akemi/brain/persona.yaml", "script.sh"])
def test_bad_paths_are_refused(path):
    with pytest.raises(PatchError):
        check_path(path)


def test_edit_must_match_exactly_once(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")
    p = parse_plan("TITULO: t\n=== EDITAR a.py\n<<<<<<< BUSCAR\nx = 1\n=======\nx = 2\n>>>>>>> FIM\n")
    with pytest.raises(PatchError, match="2 vez"):
        apply_plan(p, tmp_path)
    assert (tmp_path / "a.py").read_text() == "x = 1\nx = 1\n"  # tudo ou nada


def test_create_and_edit_in_one_plan(tmp_path):
    (tmp_path / "a.py").write_text("import os\n")
    p = parse_plan(
        "TITULO: novo\nDESCRICAO: d\n\n=== CRIAR pacote/b.py\nVALOR = 2\n=== FIM\n\n"
        "=== EDITAR a.py\n<<<<<<< BUSCAR\nimport os\n=======\nimport os\nfrom pacote.b import VALOR\n>>>>>>> FIM\n"
    )
    assert apply_plan(p, tmp_path) == ["a.py", "pacote/b.py"]
    assert (tmp_path / "pacote" / "b.py").read_text() == "VALOR = 2\n"


def test_keeps_windows_line_endings(tmp_path):
    (tmp_path / "w.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    p = parse_plan("TITULO: t\n=== EDITAR w.py\n<<<<<<< BUSCAR\nb = 2\n=======\nb = 3\n>>>>>>> FIM\n")
    apply_plan(p, tmp_path)
    assert (tmp_path / "w.py").read_bytes() == b"a = 1\r\nb = 3\r\n"


# =================================================================== na Akemi
def test_oficina_needs_to_be_turned_on(tmp_path):
    s = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path)
    akemi = Akemi(s, fast=EchoProvider(), persist=False, desktop_actions=False)
    assert akemi.oficina is None and akemi.actions.get("abrir_pr") is None


def test_opening_a_pr_always_asks_and_is_never_initiative(tmp_path, repos):
    _, work = repos
    s = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path, oficina_enabled=True,
                 github_token="tok", oficina_repo_dir=work, initiative_actions=["criar_tarefa", "abrir_pr"])
    akemi = Akemi(s, fast=EchoProvider(), persist=False, desktop_actions=False)
    action = akemi.actions.get("abrir_pr")
    assert akemi.oficina is not None and action is not None
    assert action.tier.name == "CONFIRM"
    assert "abrir_pr" not in akemi.actions.initiative  # ela não abre PR sem você dizer "sim"
