"""Assinatura: diferencia a Akemi oficial de uma modificada.

O CI assina cada build oficial (``.exe`` e imagem Docker) com uma chave
Ed25519 que só o dono do projeto tem. A assinatura cobre a impressão digital
(SHA-256) de cada arquivo do código. Ao ligar, a Akemi confere a si mesma:

- **oficial**: assinada com uma chave de :mod:`akemi.chave_oficial` e nenhum
  arquivo mudou
- **modificada**: a assinatura é oficial, mas arquivos mudaram, sumiram ou
  apareceram depois do build (ou a assinatura foi adulterada)
- **outra assinatura**: assinada por outra chave (um fork que assina os próprios builds)
- **código-fonte**: sem assinatura, rodando do git. Normal para quem desenvolve

O que isso garante, e o que não garante: o código é aberto, então uma cópia
modificada pode apagar esta checagem ou mentir sobre o resultado. O que ela
**não** consegue é produzir uma assinatura válida da chave oficial. Por isso
"oficial" vale de verdade quando quem confere é uma Akemi (ou um
``akemi-assinatura verificar``) em que você confia, olhando outra pasta.

Uso::

    akemi-assinatura                       # confere esta Akemi
    akemi-assinatura verificar <pasta>     # confere outra (pasta do .exe ou do pacote)
    akemi-assinatura gerar-chave           # cria o par de chaves (uma vez, no seu PC)
    akemi-assinatura assinar [<pasta>]     # o CI assina o build (AKEMI_SIGNING_KEY)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from akemi import __version__

SIGNATURE_FILE = "_assinatura.json"  # no pacote; ao lado do .exe é "assinatura.json"
FROZEN_SIGNATURE_FILE = "assinatura.json"
FORMAT = "akemi-assinatura"
SOURCE_SUFFIXES = (".py", ".yaml")

OFICIAL = "oficial"
MODIFICADA = "modificada"
OUTRA = "outra_assinatura"
FONTE = "codigo_fonte"
NAO_CONFERIDA = "nao_conferida"


class SignatureError(ValueError):
    pass


# ================================================================= arquivos
def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def default_root() -> Path:
    """O que é "o código" desta Akemi: a pasta do .exe ou a do pacote ``akemi``."""
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _signature_path(root: Path) -> Path:
    return root / (FROZEN_SIGNATURE_FILE if (root / "_internal").is_dir() else SIGNATURE_FILE)


def code_files(root: Path) -> list[Path]:
    """Os arquivos que a assinatura cobre.

    Na pasta do .exe: o ``.exe`` e tudo em ``_internal`` (o ``.env``, a pasta
    ``data/`` e o que você puser ao lado ficam de fora). No pacote: os ``.py``
    e ``.yaml``, sem ``__pycache__``.
    """
    root = Path(root)
    if (root / "_internal").is_dir():
        files = [p for p in root.glob("*.exe") if p.is_file()]
        files += [p for p in (root / "_internal").rglob("*") if p.is_file()]
    else:
        files = [p for p in root.rglob("*") if p.is_file() and p.suffix in SOURCE_SUFFIXES
                 and "__pycache__" not in p.parts]
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def hash_files(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): _sha256(p) for p in code_files(root)}


def fingerprint(hashes: dict[str, str]) -> str:
    """Uma impressão digital só para o código inteiro (mostra os 12 primeiros)."""
    lines = "".join(f"{k}:{v}\n" for k, v in sorted(hashes.items()))
    return hashlib.sha256(lines.encode()).hexdigest()


def _canonical(manifest: dict) -> bytes:
    body = {k: v for k, v in manifest.items() if k != "assinatura"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _crypto():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - cryptography é dependência
        raise SignatureError("falta o pacote cryptography para conferir a assinatura") from exc
    return Ed25519PrivateKey, Ed25519PublicKey, InvalidSignature


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _unb64(text: str, size: int) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise SignatureError("chave ou assinatura em base64 inválida") from exc
    if len(raw) != size:
        raise SignatureError(f"tamanho errado: {len(raw)} bytes, esperava {size}")
    return raw


# ============================================================ chaves e assinar
def generate_keys() -> tuple[str, str]:
    """(privada, pública), em base64. A privada é a semente de 32 bytes."""
    from cryptography.hazmat.primitives import serialization

    private_cls, _, _ = _crypto()
    key = private_cls.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return _b64(private), _b64(public)


def _public_of(private_b64: str) -> tuple[object, str]:
    from cryptography.hazmat.primitives import serialization

    private_cls, _, _ = _crypto()
    key = private_cls.from_private_bytes(_unb64(private_b64, 32))
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, _b64(public)


def sign(root: Path, private_b64: str, *, commit: str = "", version: str = __version__) -> dict:
    """Grava a assinatura do código em ``root`` e devolve o manifesto."""
    root = Path(root)
    key, public = _public_of(private_b64)
    sig_path = _signature_path(root)
    sig_path.unlink(missing_ok=True)  # não assina a assinatura antiga
    hashes = hash_files(root)
    if not hashes:
        raise SignatureError(f"nenhum arquivo de código em {root}")
    manifest = {
        "formato": FORMAT,
        "versao": version,
        "commit": commit,
        "criado_em": time.time(),
        "chave": public,
        "impressao": fingerprint(hashes),
        "arquivos": hashes,
    }
    manifest["assinatura"] = _b64(key.sign(_canonical(manifest)))
    sig_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


# ================================================================ conferir
@dataclass
class Check:
    status: str
    version: str = ""
    commit: str = ""
    fingerprint: str = ""
    changed: list[str] = field(default_factory=list)  # mudaram
    missing: list[str] = field(default_factory=list)  # sumiram
    added: list[str] = field(default_factory=list)  # apareceram
    detail: str = ""

    @property
    def official(self) -> bool:
        return self.status == OFICIAL

    def short(self) -> str:
        fp = f", impressão {self.fingerprint[:12]}" if self.fingerprint else ""
        ver = f" v{self.version}" if self.version else ""
        if self.status == OFICIAL:
            return f"oficial{ver}{fp}"
        if self.status == MODIFICADA:
            n = len(self.changed) + len(self.missing) + len(self.added)
            return f"modificada{ver} ({self.detail or f'{n} arquivo(s) diferente(s) do oficial'})"
        if self.status == OUTRA:
            return f"assinada por outra chave{ver}{fp} (não é a oficial)"
        if self.status == FONTE:
            return f"código-fonte{ver}, sem assinatura (rodando do git){fp}"
        return f"não deu para conferir ({self.detail})"

    def describe(self) -> str:
        lines = [f"  código: {self.short()}"]
        if self.commit:
            lines.append(f"  commit: {self.commit[:12]}")
        for label, items in (("mudaram", self.changed), ("sumiram", self.missing), ("apareceram", self.added)):
            if items:
                more = f" (+{len(items) - 8})" if len(items) > 8 else ""
                lines.append(f"  {label}: {', '.join(items[:8])}{more}")
        return "\n".join(lines)

    def for_prompt(self) -> str:
        """Uma linha para ela saber o que é (e contar a verdade se perguntarem)."""
        if self.status == OFICIAL:
            return f"Seu código é a Akemi oficial, assinada (v{self.version})."
        if self.status == MODIFICADA:
            return ("Seu código foi modificado depois da versão oficial assinada. "
                    "Se perguntarem se você é a oficial, diga a verdade: é uma versão modificada.")
        if self.status == OUTRA:
            return "Seu código foi assinado por outra pessoa, não pela chave oficial: você é uma versão derivada."
        if self.status == FONTE:
            return "Você está rodando do código-fonte (sem assinatura oficial): uma versão de desenvolvimento."
        return ""

    def to_dict(self) -> dict:
        return {"status": self.status, "versao": self.version, "commit": self.commit,
                "impressao": self.fingerprint, "diferentes": len(self.changed) + len(self.missing) + len(self.added)}


def verify(root: Path | None = None, keys: tuple[str, ...] | None = None) -> Check:
    """Confere o código em ``root`` contra a assinatura dele."""
    if keys is None:
        from akemi.chave_oficial import PUBLIC_KEYS

        keys = PUBLIC_KEYS
    root = Path(root) if root is not None else default_root()
    sig_path = _signature_path(root)
    if not sig_path.exists():
        hashes = hash_files(root)
        return Check(FONTE, version=__version__, fingerprint=fingerprint(hashes) if hashes else "")
    try:
        _, public_cls, invalid = _crypto()
    except SignatureError as exc:
        return Check(NAO_CONFERIDA, detail=str(exc))
    try:
        manifest = json.loads(sig_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("formato") != FORMAT:
            raise SignatureError("não é uma assinatura da Akemi")
        signed = manifest.get("arquivos")
        if not isinstance(signed, dict):
            raise SignatureError("assinatura sem a lista de arquivos")
        public = str(manifest.get("chave", ""))
        public_cls.from_public_bytes(_unb64(public, 32)).verify(
            _unb64(str(manifest.get("assinatura", "")), 64), _canonical(manifest)
        )
    except (ValueError, SignatureError, invalid) as exc:
        detail = "a assinatura não confere: o arquivo de assinatura foi adulterado"
        if isinstance(exc, SignatureError):
            detail = f"assinatura inválida: {exc}"
        return Check(MODIFICADA, version=__version__, detail=detail)

    check = Check(OUTRA if public not in keys else OFICIAL, version=str(manifest.get("versao", "")),
                  commit=str(manifest.get("commit", "")), fingerprint=str(manifest.get("impressao", "")))
    current = hash_files(root)
    check.changed = sorted(k for k in signed if k in current and current[k] != signed[k])
    check.missing = sorted(k for k in signed if k not in current)
    check.added = sorted(k for k in current if k not in signed)
    if check.changed or check.missing or check.added:
        check.status = MODIFICADA
    return check


_cached: Check | None = None


def self_check(refresh: bool = False) -> Check:
    """A checagem desta Akemi (calculada uma vez: o .exe pode ter muitos MB)."""
    global _cached
    if _cached is None or refresh:
        try:
            _cached = verify()
        except OSError as exc:
            _cached = Check(NAO_CONFERIDA, detail=str(exc))
    return _cached


# ===================================================================== CLI
def _write_public_key(public: str) -> Path:
    path = Path(__file__).resolve().parent / "chave_oficial.py"
    text = path.read_text(encoding="utf-8")
    head = text.split("PUBLIC_KEYS")[0]
    from akemi.chave_oficial import PUBLIC_KEYS

    keys = tuple(dict.fromkeys([*PUBLIC_KEYS, public]))
    body = "PUBLIC_KEYS: tuple[str, ...] = (\n" + "".join(f'    "{k}",\n' for k in keys) + ")\n"
    path.write_text(head + body, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="akemi-assinatura",
                                     description="Confere se a Akemi é a oficial ou foi modificada")
    sub = parser.add_subparsers(dest="cmd")
    p_ver = sub.add_parser("verificar", help="confere esta Akemi ou a de outra pasta")
    p_ver.add_argument("pasta", nargs="?", type=Path)
    sub.add_parser("gerar-chave", help="cria o par de chaves (uma vez, no seu PC)")
    p_sig = sub.add_parser("assinar", help="assina o build (usa AKEMI_SIGNING_KEY)")
    p_sig.add_argument("pasta", nargs="?", type=Path)
    args = parser.parse_args(argv)

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: S110
            pass
    try:
        if args.cmd == "gerar-chave":
            private, public = generate_keys()
            path = _write_public_key(public)
            print(f"Chave pública gravada em {path}. Faça commit desse arquivo.\n")
            print("Chave PRIVADA (cole no GitHub: Settings → Secrets and variables → Actions →")
            print("New repository secret, nome AKEMI_SIGNING_KEY). Não salve em arquivo nem mande para ninguém:\n")
            print(f"    {private}\n")
            return 0
        if args.cmd == "assinar":
            private = os.environ.get("AKEMI_SIGNING_KEY", "").strip()
            if not private:
                print("Sem AKEMI_SIGNING_KEY: build sem assinatura oficial.")
                return 0  # PR de fork ou chave ainda não criada: não quebra o build
            root = args.pasta or default_root()
            manifest = sign(root, private, commit=os.environ.get("GITHUB_SHA", ""))
            print(f"Assinado: {len(manifest['arquivos'])} arquivos, impressão {manifest['impressao'][:12]}")
            return 0
        root = getattr(args, "pasta", None)
        check = verify(root)
        print(check.describe())
        return 0 if check.status in (OFICIAL, FONTE) else 1
    except SignatureError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
