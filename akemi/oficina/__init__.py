"""Oficina: a Akemi mexe no próprio código e abre um Pull Request.

Fluxo (``Oficina.propose``)::

    pedido do dono ─► cópia isolada do repositório (git worktree da main)
                   ─► ela escolhe os arquivos que precisa ler
                   ─► escreve a mudança (formato em akemi/oficina/patch.py)
                   ─► travas: caminhos, arquivos protegidos, tamanho
                   ─► roda os testes (sem as suas chaves no ambiente)
                        └─ falhou? ela lê o erro e tenta de novo (até 2 vezes)
                   ─► varre o diff atrás de chaves, e-mails e CPFs
                   ─► push numa branch akemi/... e abre o PR como rascunho

O que ela nunca faz: juntar (merge) o PR, mexer na ``main`` ou na sua cópia
de trabalho, mexer nos arquivos protegidos ou abrir mais de
``OFICINA_PER_DAY`` PRs por dia. Quem decide se entra é sempre você, no
GitHub.

Ela só começa quando você pede (ação ``abrir_pr``, que pede "sim", ou
``/pr`` no console). O pedido e o código do repositório são tudo o que vai
para o modelo: nada da memória, da tela ou do chat.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
import structlog

from akemi.brain.attention import parse_json_object
from akemi.brain.providers.base import LLMProvider, Message, ProviderError

from .patch import ALLOWED_SUFFIXES, PROTECTED, PatchError, apply_plan, leaks, parse_plan

logger = structlog.get_logger()

LOG_FILE = "oficina.json"
MAX_READ_FILES = 6
MAX_FILE_CHARS = 20_000
MAX_CONTEXT_CHARS = 60_000
SECRETISH_ENV = re.compile(r"(TOKEN|KEY|SECRET|PASSW|GITHUB|NVIDIA|OPENAI|ANTHROPIC|CHIP)", re.I)

FORMAT_HELP = """Responda SOMENTE neste formato, sem markdown em volta:

TITULO: frase curta do que muda
DESCRICAO: duas ou três frases explicando a mudança

=== EDITAR caminho/do/arquivo.py
<<<<<<< BUSCAR
trecho que já existe no arquivo, copiado exatamente (com a indentação)
=======
trecho novo que entra no lugar
>>>>>>> FIM

=== CRIAR caminho/novo_arquivo.py
conteúdo inteiro do arquivo novo
=== FIM

Pode repetir EDITAR (com vários blocos BUSCAR) e CRIAR quantas vezes precisar.
O trecho de BUSCAR precisa aparecer exatamente uma vez no arquivo."""


class OficinaError(RuntimeError):
    pass


@dataclass
class Result:
    status: str  # aberto | falhou | recusado
    request: str
    title: str = ""
    url: str = ""
    branch: str = ""
    detail: str = ""
    files: list[str] = field(default_factory=list)
    at: float = field(default_factory=time.time)

    def describe(self) -> str:
        when = time.strftime("%d/%m %H:%M", time.localtime(self.at))
        what = self.title or self.request[:60]
        extra = self.url or self.detail
        return f"[{when}] {self.status}: {what}" + (f" ({extra})" if extra else "")


def slugify(text: str, limit: int = 32) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:limit].rstrip("-") or "mudanca"


def scrubbed_env() -> dict[str, str]:
    """Ambiente para rodar os testes: sem as suas chaves e tokens."""
    env = {k: v for k, v in os.environ.items() if not SECRETISH_ENV.search(k)}
    env["AKEMI_OFICINA"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def auth_env(token: str) -> dict[str, str]:
    """Token do GitHub para o git, pelo ambiente (não fica em URL, argumento nem config)."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {basic}"
    return env


async def run(args: list[str], cwd: Path, env: dict[str, str] | None = None, timeout: float = 120.0) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"passou do tempo limite ({int(timeout)} s)"
    return proc.returncode or 0, out.decode("utf-8", "replace")


def default_test_cmd() -> list[str]:
    """Roda o pytest do repositório. No Akemi.exe não há pytest dentro: usa o Python do PC."""
    python = sys.executable
    if getattr(sys, "frozen", False):
        python = shutil.which("python") or shutil.which("py") or "python"
    return [python, "-m", "pytest", "-x", "-p", "no:cacheprovider"]


class Oficina:
    def __init__(
        self,
        *,
        repo_dir: Path,
        repo: str,
        token: str,
        llm: LLMProvider,
        model: str,
        data_dir: Path,
        base: str = "main",
        per_day: int = 2,
        remote_url: str = "",
        test_cmd: list[str] | None = None,
        test_timeout_s: float = 900.0,
        fix_rounds: int = 2,
        api_url: str = "https://api.github.com",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
            raise OficinaError("GITHUB_REPO precisa ser dono/repositorio")
        self.repo_dir = Path(repo_dir).resolve()
        self.repo = repo
        self.token = token
        self.llm = llm
        self.model = model
        self.data_dir = Path(data_dir)
        self.base = base
        self.per_day = per_day
        self.remote_url = remote_url or f"https://github.com/{repo}.git"
        self.test_cmd = test_cmd or default_test_cmd()
        self.test_timeout_s = test_timeout_s
        self.fix_rounds = fix_rounds
        self.api_url = api_url.rstrip("/")
        self._http = http
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ histórico
    @property
    def log_path(self) -> Path:
        return self.data_dir / LOG_FILE

    def history(self) -> list[Result]:
        try:
            raw = json.loads(self.log_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []
        out = []
        for r in raw if isinstance(raw, list) else []:
            try:
                out.append(Result(**{k: v for k, v in r.items() if k in Result.__dataclass_fields__}))
            except TypeError:
                continue
        return out

    def _log(self, result: Result) -> None:
        items = [asdict(r) for r in self.history()][-49:] + [asdict(result)]
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.log_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.log_path)

    def opened_today(self) -> int:
        day = time.time() - 86400
        return sum(1 for r in self.history() if r.status == "aberto" and r.at >= day)

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    # ------------------------------------------------------------ pedido
    async def propose(self, request: str) -> Result:
        request = " ".join(request.split())[:500]
        if not request:
            return Result("recusado", request, detail="pedido vazio")
        if self._lock.locked():
            return Result("recusado", request, detail="já estou trabalhando em outro PR")
        if self.opened_today() >= self.per_day:
            return Result("recusado", request, detail=f"já abri {self.per_day} PR(s) nas últimas 24 h")
        async with self._lock:
            try:
                result = await self._work(request)
            except (OficinaError, ProviderError, httpx.HTTPError, OSError) as exc:
                logger.warning("oficina failed", error=str(exc))
                result = Result("falhou", request, detail=str(exc)[:300])
            self._log(result)
            return result

    async def _work(self, request: str) -> Result:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        branch = f"akemi/{slugify(request)}-{stamp}"
        work = self.data_dir / "oficina" / stamp
        work.parent.mkdir(parents=True, exist_ok=True)
        git_auth = auth_env(self.token)
        code, out = await run(["git", "fetch", self.remote_url, self.base], self.repo_dir, git_auth)
        if code:
            raise OficinaError(f"não consegui baixar a {self.base}: {out.strip()[-200:]}")
        code, out = await run(["git", "worktree", "add", "-b", branch, str(work), "FETCH_HEAD"], self.repo_dir)
        if code:
            raise OficinaError(f"não consegui criar a cópia isolada: {out.strip()[-200:]}")
        try:
            return await self._in_worktree(request, branch, work, git_auth)
        finally:
            await run(["git", "worktree", "remove", "--force", str(work)], self.repo_dir)
            await run(["git", "branch", "-D", branch], self.repo_dir)
            shutil.rmtree(work, ignore_errors=True)

    async def _in_worktree(self, request: str, branch: str, work: Path, git_auth: dict) -> Result:
        context = await self._read_context(request, work)
        messages = [
            Message("system", self._system_prompt()),
            Message("user", f"Pedido do dono: {request}\n\nArquivos do projeto:\n{context}\n\n{FORMAT_HELP}"),
        ]
        plan = None
        changed: list[str] = []
        test_out = ""
        tests_failed = False
        for attempt in range(self.fix_rounds + 1):
            raw = await self.llm.complete(messages, model=self.model, max_tokens=6000, temperature=0.2)
            await self._reset(work)
            try:
                plan = parse_plan(raw)
                changed = apply_plan(plan, work)
            except PatchError as exc:
                logger.info("oficina patch rejected", attempt=attempt, error=str(exc))
                messages += [Message("assistant", raw), Message("user", f"Não deu para aplicar: {exc}\n\n{FORMAT_HELP}")]
                plan = None
                continue
            code, test_out = await run(self.test_cmd, work, scrubbed_env(), self.test_timeout_s)
            tests_failed = code != 0
            if not tests_failed:
                break
            logger.info("oficina tests failed", attempt=attempt)
            messages += [
                Message("assistant", raw),
                Message("user", "Os testes falharam com a sua mudança. Saída (final):\n"
                        f"{test_out[-3000:]}\n\nMande a mudança inteira de novo, corrigida, a partir do "
                        f"código original.\n\n{FORMAT_HELP}"),
            ]
            plan = None
        if plan is None:
            reason = "os testes não passaram" if tests_failed else "não consegui escrever uma mudança que se aplique"
            return Result("falhou", request, detail=reason)

        await run(["git", "add", "-A"], work)
        _, diff = await run(["git", "diff", "--cached", "--unified=0"], work)
        added = "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
        if found := leaks(added + "\n" + plan.title + "\n" + plan.description):
            return Result("recusado", request, title=plan.title, detail=f"o diff tem {', '.join(found)}: não publico isso")

        passed = re.findall(r"(\d+) passed", test_out)
        body = self._pr_body(request, plan.description, changed, passed[-1] if passed else "")
        if found := leaks(body):
            return Result("recusado", request, title=plan.title, detail=f"a descrição tem {', '.join(found)}")

        ident = []
        _, email = await run(["git", "config", "user.email"], work)
        if not email.strip():
            ident = ["-c", "user.name=Akemi", "-c", "user.email=akemi@localhost.invalid"]
        msg = f"{plan.title}\n\n{plan.description}\n\nEscrito pela Akemi (IA) a pedido do dono."
        code, out = await run(["git", *ident, "commit", "-q", "-m", msg], work)
        if code:
            raise OficinaError(f"commit falhou: {out.strip()[-200:]}")
        code, out = await run(["git", "push", self.remote_url, f"HEAD:refs/heads/{branch}"], work, git_auth, timeout=180)
        if code:
            raise OficinaError(f"push falhou: {out.strip()[-200:]}")
        url = await self._open_pr(plan.title, branch, body)
        logger.info("oficina PR opened", url=url, files=changed)
        return Result("aberto", request, title=plan.title, url=url, branch=branch, files=changed)

    # ------------------------------------------------------------ partes
    def _system_prompt(self) -> str:
        protected = ", ".join(PROTECTED)
        return (
            "Você é a Akemi trabalhando no seu próprio código-fonte (Python, projeto open-source). "
            "Faça a MENOR mudança que resolve o pedido, no estilo do projeto: comentários, docstrings e textos "
            "em português do Brasil, sem dependências novas se der para evitar. Quando fizer sentido, inclua "
            "um teste em tests/ (os testes não podem usar rede, chaves nem placa de som). "
            f"Nunca mexa nestes caminhos protegidos: {protected}. "
            "Nunca coloque chaves, tokens, senhas, e-mails ou dados pessoais no código."
        )

    async def _reset(self, work: Path) -> None:
        await run(["git", "checkout", "--", "."], work)
        await run(["git", "clean", "-fdq"], work)

    async def _read_context(self, request: str, work: Path) -> str:
        _, listing = await run(["git", "ls-files"], work)
        files = [f for f in listing.splitlines() if Path(f).suffix in ALLOWED_SUFFIXES and not f.startswith(".github/")]
        ask = (
            f"Pedido do dono: {request}\n\nArquivos do projeto:\n" + "\n".join(files[:400]) +
            f"\n\nQuais arquivos você precisa ler para fazer isso (até {MAX_READ_FILES})? "
            'Responda só com JSON: {"arquivos": ["caminho", ...]}'
        )
        raw = await self.llm.complete(
            [Message("system", self._system_prompt()), Message("user", ask)],
            model=self.model, max_tokens=300, temperature=0.1, json_mode=True,
        )
        wanted = parse_json_object(raw).get("arquivos", [])
        chosen = [f for f in wanted if isinstance(f, str) and f in files][:MAX_READ_FILES]
        parts, total = [], 0
        for f in chosen:
            text = (work / f).read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
            if total + len(text) > MAX_CONTEXT_CHARS:
                break
            total += len(text)
            parts.append(f"----- {f}\n{text}")
        tree = "Lista de arquivos:\n" + "\n".join(files[:400])
        return tree + ("\n\nConteúdo dos arquivos:\n" + "\n\n".join(parts) if parts else "")

    def _pr_body(self, request: str, description: str, files: list[str], passed: str) -> str:
        tests = f"{passed} testes passaram na cópia isolada." if passed else "Os testes passaram na cópia isolada."
        return (
            f"{description}\n\n"
            f"**Pedido:** {request}\n\n"
            "**Arquivos:** " + ", ".join(f"`{f}`" for f in files) + "\n\n"
            f"**Testes:** {tests}\n\n"
            "---\n"
            "_Aberto pela Akemi (IA) a pedido do dono. Ela não junta PRs sozinha: revise antes de fazer o merge._"
        )

    async def _open_pr(self, title: str, branch: str, body: str) -> str:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        payload = {"title": title, "head": branch, "base": self.base, "body": body, "draft": True}
        client = self._http or httpx.AsyncClient(timeout=30)
        try:
            url = f"{self.api_url}/repos/{self.repo}/pulls"
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 422 and "draft" in resp.text.lower():
                payload["draft"] = False  # repositório sem suporte a rascunho
                resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                raise OficinaError(f"o GitHub recusou o PR: HTTP {resp.status_code} {resp.text[:200]}")
            return str(resp.json().get("html_url", ""))
        finally:
            if self._http is None:
                await client.aclose()
