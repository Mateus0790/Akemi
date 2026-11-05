"""As mudanças que ela escreve no próprio código, e as travas antes de aplicar.

O modelo responde num formato de texto simples (mais fácil para ele do que
JSON com código dentro)::

    TITULO: diz a previsão do tempo
    DESCRICAO: nova ação "clima" que consulta a previsão.

    === EDITAR akemi/actions/builtin.py
    <<<<<<< BUSCAR
    trecho exato que já existe
    =======
    trecho novo
    >>>>>>> FIM

    === CRIAR akemi/actions/clima.py
    conteúdo inteiro do arquivo novo
    === FIM

Travas, todas em código:

- Só arquivos dentro do repositório, com extensões de texto conhecidas.
- **Arquivos protegidos não mudam**: a segurança dela (moderação, permissões
  de ações, filtros de lealdade, autenticação do núcleo), a própria oficina e
  a pasta ``.github`` (um workflow alterado poderia vazar os segredos do CI).
- O trecho de BUSCAR tem que existir exatamente uma vez.
- Limite de arquivos e de tamanho por PR.
- O diff final é varrido atrás de chaves, tokens, e-mails e CPFs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from akemi.safety.moderation import CPF_RE, EMAIL_RE, SECRET_RE

MAX_FILES = 6
MAX_CHANGE_CHARS = 60_000
ALLOWED_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".json", ".dart"}
# Prefixos/arquivos que ela nunca mexe sozinha (posix, relativos à raiz do repositório).
PROTECTED = (
    ".github/",
    ".git/",
    "akemi/safety/",
    "akemi/oficina/",
    "akemi/actions/registry.py",
    "akemi/brain/self_improve.py",
    "akemi/brain/will.py",
    "akemi/audio/voice_id.py",
    "akemi/net/server.py",
    "akemi/net/companion.py",
    "akemi/net/protocol.py",
    "akemi/storage/chip.py",
    "akemi/brain/persona.yaml",
    "data/",
    ".env",
    "Dockerfile",
    "docker-compose.yml",
)

HEADER_RE = re.compile(r"^(TITULO|DESCRICAO)\s*:\s*(.*)$", re.M)
BLOCK_RE = re.compile(r"^=== (EDITAR|CRIAR) +(\S+)\s*$", re.M)
EDIT_RE = re.compile(r"<<<<<<< BUSCAR\n(.*?)\n=======\n(.*?)\n?>>>>>>> FIM", re.S)


class PatchError(ValueError):
    pass


@dataclass
class Change:
    kind: str  # editar | criar
    path: str
    edits: list[tuple[str, str]] = field(default_factory=list)  # (buscar, substituir)
    content: str = ""


@dataclass
class Plan:
    title: str
    description: str
    changes: list[Change]


def check_path(path: str) -> str:
    """Normaliza e confere um caminho vindo do modelo. Devolve o caminho posix."""
    raw = path.strip().strip("`").replace("\\", "/")
    p = PurePosixPath(raw)
    if not raw or p.is_absolute() or ".." in p.parts or raw.startswith("~"):
        raise PatchError(f"caminho inválido: {path}")
    norm = str(p)
    for prot in PROTECTED:
        if norm == prot.rstrip("/") or norm.startswith(prot):
            raise PatchError(f"{norm} é protegido: ela não mexe nisso sozinha")
    if p.name.startswith(".env"):
        raise PatchError(f"{norm} é protegido: ela não mexe nisso sozinha")
    if p.suffix not in ALLOWED_SUFFIXES:
        raise PatchError(f"tipo de arquivo não permitido: {norm}")
    return norm


def parse_plan(text: str) -> Plan:
    headers = {m.group(1): m.group(2).strip() for m in HEADER_RE.finditer(text)}
    title = headers.get("TITULO", "").strip()[:80]
    if not title:
        raise PatchError("faltou o TITULO")
    blocks = list(BLOCK_RE.finditer(text))
    if not blocks:
        raise PatchError("nenhuma mudança proposta")
    changes: list[Change] = []
    for i, m in enumerate(blocks):
        end = blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
        body = text[m.end():end]
        kind, path = m.group(1), check_path(m.group(2))
        if kind == "EDITAR":
            edits = [(a, b) for a, b in EDIT_RE.findall(body)]
            if not edits:
                raise PatchError(f"{path}: EDITAR sem bloco BUSCAR/FIM")
            changes.append(Change("editar", path, edits=edits))
        else:
            content = re.split(r"^=== FIM\s*$", body.lstrip("\n"), maxsplit=1, flags=re.M)[0]
            if not content.strip():
                raise PatchError(f"{path}: arquivo novo vazio")
            changes.append(Change("criar", path, content=content.rstrip("\n") + "\n"))
    if len({c.path for c in changes}) > MAX_FILES:
        raise PatchError(f"mudanças demais (máximo {MAX_FILES} arquivos)")
    size = sum(len(c.content) + sum(len(a) + len(b) for a, b in c.edits) for c in changes)
    if size > MAX_CHANGE_CHARS:
        raise PatchError("mudança grande demais para um PR só")
    return Plan(title, headers.get("DESCRICAO", "").strip()[:600], changes)


def apply_plan(plan: Plan, root: Path) -> list[str]:
    """Aplica no diretório (a cópia isolada). Tudo ou nada. Devolve os arquivos mudados."""
    root = Path(root).resolve()
    staged: dict[Path, str] = {}
    crlf: set[Path] = set()  # arquivos com quebra de linha do Windows: mantém como estavam
    for ch in plan.changes:
        target = (root / ch.path).resolve()
        if root not in target.parents:
            raise PatchError(f"caminho fora do repositório: {ch.path}")
        if ch.kind == "criar":
            if target.exists() and target not in staged:
                raise PatchError(f"{ch.path} já existe: use EDITAR")
            staged[target] = ch.content
            continue
        if target in staged:
            text = staged[target]
        elif target.exists():
            text = target.read_bytes().decode("utf-8")
            if "\r\n" in text:
                crlf.add(target)
                text = text.replace("\r\n", "\n")
        else:
            raise PatchError(f"{ch.path} não existe")
        for find, repl in ch.edits:
            count = text.count(find)
            if count != 1:
                raise PatchError(
                    f"{ch.path}: o trecho a trocar aparece {count} vez(es), precisa ser exatamente 1"
                )
            text = text.replace(find, repl, 1)
        staged[target] = text
    for target, text in staged.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if target in crlf:
            text = text.replace("\n", "\r\n")
        target.write_bytes(text.encode("utf-8"))  # bytes: o Windows não troca as quebras de linha
    return sorted(str(t.relative_to(root)).replace("\\", "/") for t in staged)


def leaks(text: str) -> list[str]:
    """O que no texto parece dado sensível (para nunca ir num PR público)."""
    found = []
    if SECRET_RE.search(text):
        found.append("chave ou token")
    if EMAIL_RE.search(text):
        found.append("e-mail")
    if CPF_RE.search(text):
        found.append("CPF")
    return found
