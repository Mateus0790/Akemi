"""O construtor: ela escreve uma habilidade nova em segundo plano.

Fluxo (``Builder.build``)::

    programa (+ por onde dá para entrar, + nomes dos arquivos da pasta dele)
      ─► o modelo escreve habilidade.yaml + habilidade.py
      ─► travas (check_code) e varredura de chaves/e-mails
      ─► teste: carrega o plugin num processo separado, sem as suas chaves,
         e confere que ligar() registra alguma coisa
           └─ falhou? ela lê o erro e tenta de novo (até 2 vezes)
      ─► salva como RASCUNHO em data/habilidades/<id>/

Rascunho não roda. Quem liga é você, depois de revisar (``/habilidade ativar``).
Para o modelo vão só o nome do programa, o catálogo e os nomes dos arquivos
da pasta do programa: nada da sua memória, da tela ou do chat.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

from akemi.brain.providers.base import LLMProvider, Message, ProviderError
from akemi.oficina import run, scrubbed_env
from akemi.oficina.patch import leaks

from .atividade import Program, folder_hint
from .plugin import CODE, MANIFEST, Manifest, PluginError, check_code

logger = structlog.get_logger()

BLOCK_RE = re.compile(r"^=== (habilidade\.yaml|habilidade\.py)\s*$(.*?)(?=^=== )", re.M | re.S)

API_HELP = '''Uma habilidade é um plugin Python com a função ligar(akemi). O "akemi" tem:
- akemi.acao(nome, descricao, fn, params={"param": "descrição"}, confirmar="Pergunta?")
  cria uma ação que você pode usar na conversa. fn pode ser normal ou async e devolve um texto
  curto (o que aconteceu). Com confirmar, o dono diz "sim" antes: use para tudo que muda algo.
- akemi.ao_abrir(fn) / akemi.ao_fechar(fn): quando o programa abre/fecha (fn normal ou async).
- akemi.tarefa(fn_async): roda enquanto o programa está aberto (um loop com await asyncio.sleep).
- akemi.perceber(texto): conta algo que aconteceu no programa (ex.: "o dono morreu no jogo").
- akemi.falar(texto): fala em voz alta (use pouco).
- akemi.pasta (Path, pasta só sua para guardar dados), akemi.pastas["appdata"|"localappdata"|"casa"|"documentos"],
  akemi.programa.exe (caminho do executável, pode ser ""), akemi.log.info(...).

Regras (o código é recusado se quebrar):
- NÃO importe: akemi, subprocess, ctypes, sys, shutil, importlib, winreg, keyboard, pynput.
- NÃO use eval, exec, getattr, os.environ, os.system, os.remove, unlink, rmtree, nada com __dunder__.
- Pode usar: asyncio, json, re, time, pathlib, socket, httpx, websockets (se instalado), sqlite3, xml.
- ligar() só registra coisas: nada de rede, arquivos grandes ou espera dentro dele.
- Ler arquivos do programa (logs, saves) pode. Escrever só na akemi.pasta ou na pasta de mods/plugins
  do programa. Nunca apague nada.
- Nunca leia senhas, tokens, cookies ou dados de login. Nada de ler memória de outro processo,
  simular teclado/mouse ou contornar anti-cheat.
- Comentários e textos em português do Brasil. Prefira a ponte mais simples que funciona.

Exemplo:
=== habilidade.yaml
id: exemplo
nome: Exemplo
programa: exemplo
descricao: Lê o log do Exemplo e conta para a Akemi quando o dono perde.
ponte: arquivo de log
=== habilidade.py
import asyncio
from pathlib import Path


def ligar(akemi):
    log = akemi.pastas["appdata"] / "Exemplo" / "log.txt"

    @akemi.tarefa
    async def vigiar_log():
        pos = log.stat().st_size if log.exists() else 0
        while True:
            await asyncio.sleep(2)
            if not log.exists():
                continue
            with log.open(encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                novas = f.read()
                pos = f.tell()
            for linha in novas.splitlines():
                if "game over" in linha.lower():
                    akemi.perceber("o dono perdeu uma partida no Exemplo")

    def status():
        return "Tô de olho no log do Exemplo." if log.exists() else "Não achei o log do Exemplo."

    akemi.acao("status", "diz se ela está conseguindo acompanhar o Exemplo", status)
=== FIM'''

FORMAT_HELP = (
    "Responda SOMENTE com os dois arquivos, neste formato, sem markdown em volta:\n"
    "=== habilidade.yaml\n...\n=== habilidade.py\n...\n=== FIM"
)

# Carrega o plugin num processo separado com um "akemi" de mentira e conta o que ele registrou.
RUNNER = r'''
import asyncio, importlib.util, json, sys
from pathlib import Path
folder = Path(sys.argv[1])
class Prog:
    key = name = "teste"; exe = ""; bridges = ()
class Log:
    def info(self, *a, **k): pass
    warning = error = exception = debug = info
class Ctx:
    def __init__(self):
        self.out = {"acoes": [], "abrir": 0, "fechar": 0, "tarefas": 0}
        self.pasta = folder / "dados"; self.programa = Prog(); self.log = Log()
        home = Path.home()
        self.pastas = {"casa": home, "appdata": home, "localappdata": home, "documentos": home}
    def acao(self, nome, descricao, fn, params=None, confirmar=""):
        if not callable(fn): raise TypeError("fn da ação " + str(nome) + " não é função")
        if not isinstance(descricao, str) or not descricao: raise TypeError("ação sem descrição")
        self.out["acoes"].append(str(nome)); return str(nome)
    def ao_abrir(self, fn): self.out["abrir"] += 1; return fn
    def ao_fechar(self, fn): self.out["fechar"] += 1; return fn
    def tarefa(self, fn):
        if not asyncio.iscoroutinefunction(fn): raise TypeError("tarefa precisa ser async def")
        self.out["tarefas"] += 1; return fn
    def falar(self, t): pass
    def perceber(self, t): pass
spec = importlib.util.spec_from_file_location("habilidade_teste", folder / "habilidade.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
ctx = Ctx(); mod.ligar(ctx)
print("RESULTADO " + json.dumps(ctx.out))
'''


@dataclass
class BuildResult:
    status: str  # rascunho | falhou | recusado
    program: str
    id: str = ""
    detail: str = ""
    summary: str = ""


def parse_files(raw: str) -> tuple[Manifest, str]:
    text = raw.replace("\r\n", "\n")
    text = re.sub(r"^```\w*\n|^```\s*$", "", text, flags=re.M)
    if "=== FIM" not in text:
        text += "\n=== FIM\n"
    files = {m.group(1): m.group(2).strip("\n") + "\n" for m in BLOCK_RE.finditer(text)}
    if MANIFEST not in files or CODE not in files:
        raise PluginError("faltou o habilidade.yaml ou o habilidade.py")
    return Manifest.parse(files[MANIFEST]), files[CODE]


def can_test_in_subprocess() -> bool:
    return not getattr(sys, "frozen", False)  # no Akemi.exe não há um Python para rodar o teste


class Builder:
    def __init__(self, llm: LLMProvider, model: str, root: Path, fix_rounds: int = 2,
                 test_timeout_s: float = 30.0) -> None:
        self.llm = llm
        self.model = model
        self.root = Path(root)
        self.fix_rounds = fix_rounds
        self.test_timeout_s = test_timeout_s
        self._lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def build(self, program: Program, wish: str = "") -> BuildResult:
        if self._lock.locked():
            return BuildResult("recusado", program.name, detail="já estou criando outra habilidade")
        async with self._lock:
            try:
                return await self._work(program, wish)
            except (ProviderError, OSError) as exc:
                logger.warning("habilidade build failed", program=program.key, error=str(exc))
                return BuildResult("falhou", program.name, detail=str(exc)[:300])

    def _prompt(self, program: Program, wish: str) -> str:
        bridges = "\n".join(f"- {b}" for b in program.bridges) or "- (programa fora do catálogo: use o que você sabe dele)"
        hint = folder_hint(program.exe)
        parts = [
            f"Programa: {program.name} (chave: {program.key})",
            f"Executável: {program.exe or 'não sei onde está'}",
            f"Por onde dá para entrar, do mais fácil para o mais difícil:\n{bridges}",
        ]
        if hint:
            parts.append(f"Arquivos na pasta do programa (só os nomes): {hint}")
        if wish:
            parts.append(f"O dono pediu: {wish[:300]}")
        parts.append(
            f"Escreva uma habilidade com id \"{program.key}\" e programa \"{program.key}\" que deixe a Akemi "
            "acompanhar e participar desse programa pela ponte mais simples que funcione de verdade. "
            "Inclua pelo menos uma ação de status. Se não houver ponte segura, faça só a leitura de logs."
        )
        return "\n\n".join(parts) + f"\n\n{FORMAT_HELP}"

    async def _work(self, program: Program, wish: str) -> BuildResult:
        system = (
            "Você é a Akemi criando uma habilidade nova para si mesma: um plugin pequeno, seguro e que "
            "funciona. Escreva código simples e defensivo (arquivo pode não existir, programa pode fechar).\n\n"
            + API_HELP
        )
        messages = [Message("system", system), Message("user", self._prompt(program, wish))]
        last_error = ""
        for attempt in range(self.fix_rounds + 1):
            raw = await self.llm.complete(messages, model=self.model, max_tokens=5000, temperature=0.2)
            try:
                manifest, code = parse_files(raw)
                if manifest.id != program.key or manifest.programa != program.key:
                    manifest.id = manifest.programa = program.key
                if problems := check_code(code):
                    raise PluginError("; ".join(problems))
                if found := leaks(code + manifest.dump()):
                    return BuildResult("recusado", program.name, detail=f"o código tem {', '.join(found)}")
                summary = await self._test(manifest, code)
            except PluginError as exc:
                last_error = str(exc)
                logger.info("habilidade rejected", program=program.key, attempt=attempt, error=last_error)
                messages += [
                    Message("assistant", raw),
                    Message("user", f"Não passou: {last_error[:2000]}\n\nMande os dois arquivos de novo, corrigidos.\n\n{FORMAT_HELP}"),
                ]
                continue
            self._save(manifest, code)
            return BuildResult("rascunho", program.name, manifest.id, summary=summary)
        return BuildResult("falhou", program.name, detail=last_error[:300] or "não consegui escrever")

    async def _test(self, manifest: Manifest, code: str) -> str:
        if not can_test_in_subprocess():
            compile(code, CODE, "exec")
            return "passou nas travas (sem teste em processo separado no .exe)"
        with tempfile.TemporaryDirectory(prefix="akemi-habilidade-") as tmp:
            folder = Path(tmp)
            (folder / CODE).write_text(code, encoding="utf-8")
            status, out = await run([sys.executable, "-I", "-c", RUNNER, str(folder)], folder,
                                    scrubbed_env(), self.test_timeout_s)
        found = re.search(r"^RESULTADO (\{.*\})$", out, re.M)
        if status or not found:
            raise PluginError(f"o teste falhou:\n{out[-1500:]}")
        info = json.loads(found.group(1))
        if not (info["acoes"] or info["abrir"] or info["tarefas"]):
            raise PluginError("ligar() não registrou nada (nenhuma ação, rotina ou reação ao abrir)")
        bits = []
        if info["acoes"]:
            bits.append("ações: " + ", ".join(info["acoes"]))
        if info["tarefas"]:
            bits.append(f"{info['tarefas']} rotina(s) enquanto o programa está aberto")
        if info["abrir"] or info["fechar"]:
            bits.append("reage quando o programa abre/fecha")
        return "; ".join(bits)

    def _save(self, manifest: Manifest, code: str) -> Path:
        folder = self.root / manifest.id
        draft = self.root / f".{manifest.id}.novo"
        shutil.rmtree(draft, ignore_errors=True)
        draft.mkdir(parents=True)
        (draft / MANIFEST).write_text(manifest.dump(), encoding="utf-8")
        (draft / CODE).write_text(code, encoding="utf-8")
        if folder.exists():
            keep = folder / "dados"
            if keep.exists():
                keep.rename(draft / "dados")  # o que a habilidade já guardou fica
            shutil.rmtree(folder)
        draft.rename(folder)
        return folder
