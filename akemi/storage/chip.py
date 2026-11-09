"""Chip de identidade: a Akemi inteira num arquivo só.

Tudo o que faz ela ser *ela* (e não o modelo de linguagem que roda por baixo):

- ``persona.yaml``  — o jeito dela
- ``evolucao.json`` — o que ela aprendeu e você aprovou
- ``state.json``    — humor, objetivo, tarefas, pensamentos, último corpo
- ``memory.sqlite3`` — conversas e fatos
- ``voz.json``      — a impressão da voz do dono (opcional, sem áudio)
- ``vida.json``     — planos e datas importantes do dono que ela anotou

Com o chip dá para levar a Akemi para outro núcleo (outro PC, outro servidor)
ou trocar o modelo (NIM, Ollama, Anthropic...) sem que ela perca quem é::

    akemi-chip exportar akemi.chip
    akemi-chip inspecionar akemi.chip
    akemi-chip importar akemi.chip      # com a Akemi desligada

O chip é um zip com ``manifest.json``: SHA-256 de cada arquivo e, se houver
``CHIP_KEY`` no .env, uma assinatura HMAC. Com ``CHIP_KEY`` definida, só entram
chips assinados com ela. A importação guarda uma cópia do que existia antes em
``data/backup-<data>/``.

⚠️ O chip contém suas conversas. Trate como dado pessoal: não publique.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

FORMAT = "akemi-chip"
VERSION = 1
MANIFEST = "manifest.json"
PERSONA, EVOLUTION, STATE, MEMORY = "persona.yaml", "evolucao.json", "state.json", "memory.sqlite3"
VOICE = "voz.json"  # impressão da voz do dono (sem áudio), se cadastrada
LIFE = "vida.json"  # combinados e datas importantes do dono
FILES = (PERSONA, EVOLUTION, STATE, MEMORY, VOICE, LIFE)  # nomes fixos: nada de caminhos vindos do zip
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_SMALL_BYTES = 2 * 1024 * 1024  # persona, estado e evolução são pequenos


class ChipError(ValueError):
    pass


@dataclass
class ChipInfo:
    name: str
    owner: str
    created_at: float
    origin: str
    files: dict[str, str]
    signed: bool
    verified: bool
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        when = time.strftime("%d/%m/%Y %H:%M", time.localtime(self.created_at))
        sig = "assinado e verificado" if self.verified else ("assinado (sem chave para conferir)" if self.signed else "sem assinatura")
        lines = [
            f"Chip de {self.name} (dono: {self.owner})",
            f"Criado em {when} no núcleo {self.origin}",
            f"Assinatura: {sig}",
            "Conteúdo: " + ", ".join(sorted(self.files)),
        ]
        lines += [f"Aviso: {w}" for w in self.warnings]
        return "\n".join(lines)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _signature(manifest: dict, key: str) -> str:
    body = {k: v for k, v in manifest.items() if k != "assinatura"}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hmac.new(key.encode(), canonical, hashlib.sha256).hexdigest()


# =============================================================== exportar
def export_chip(
    out: Path,
    *,
    data_dir: Path,
    persona_file: Path,
    owner: str,
    key: str = "",
    memory=None,
) -> ChipInfo:
    """Grava o chip. ``memory`` (um ``Memory`` aberto) permite exportar com ela rodando."""
    out = Path(out)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        staged: dict[str, Path] = {}

        from akemi.brain.persona import DEFAULT_PERSONA_PATH

        persona_src = data_dir / PERSONA if (data_dir / PERSONA).exists() else Path(persona_file)
        if not persona_src.exists():
            persona_src = DEFAULT_PERSONA_PATH  # mesma regra do Persona.load
        staged[PERSONA] = Path(persona_src)
        for name in (EVOLUTION, STATE, VOICE, LIFE):
            if (data_dir / name).exists():
                staged[name] = data_dir / name

        mem_copy = tmpdir / MEMORY
        if memory is not None:
            memory.backup_to(mem_copy)
            staged[MEMORY] = mem_copy
        elif (data_dir / MEMORY).exists():
            src = sqlite3.connect(f"file:{data_dir / MEMORY}?mode=ro", uri=True)
            dest = sqlite3.connect(str(mem_copy))
            try:
                src.backup(dest)
            finally:
                src.close()
                dest.close()
            staged[MEMORY] = mem_copy

        name = "Akemi"
        try:
            name = str((yaml.safe_load(staged[PERSONA].read_text(encoding="utf-8")) or {}).get("name") or name)
        except yaml.YAMLError as exc:
            raise ChipError(f"persona inválida: {exc}") from exc

        manifest = {
            "formato": FORMAT,
            "versao": VERSION,
            "nome": name,
            "dono": owner,
            "criado_em": time.time(),
            "origem": socket.gethostname(),
            "arquivos": {n: _sha256(p) for n, p in staged.items()},
            "assinatura": None,
        }
        if key:
            manifest["assinatura"] = _signature(manifest, key)

        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_suffix(out.suffix + ".part")
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            for n, p in staged.items():
                zf.write(p, n)
        part.replace(out)
    return _info(manifest, signed=bool(key), verified=bool(key))


# ============================================================ inspecionar
def _info(manifest: dict, *, signed: bool, verified: bool, warnings=None) -> ChipInfo:
    return ChipInfo(
        name=str(manifest.get("nome", "?")),
        owner=str(manifest.get("dono", "?")),
        created_at=float(manifest.get("criado_em", 0) or 0),
        origin=str(manifest.get("origem", "?")),
        files=dict(manifest.get("arquivos", {})),
        signed=signed,
        verified=verified,
        warnings=list(warnings or []),
    )


def _validate_contents(files: dict[str, Path]) -> None:
    """Confere que cada parte é o que diz ser antes de instalar qualquer coisa."""
    from akemi.brain.self_improve import Evolution, EvolutionError
    from akemi.brain.state import InnerState

    try:
        persona = yaml.safe_load(files[PERSONA].read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ChipError(f"persona inválida: {exc}") from exc
    if not isinstance(persona, dict) or not isinstance(persona.get("identity", ""), str):
        raise ChipError("persona inválida")
    if STATE in files:
        try:
            InnerState.from_dict(json.loads(files[STATE].read_text(encoding="utf-8")))
        except (ValueError, TypeError) as exc:
            raise ChipError(f"estado inválido: {exc}") from exc
    if EVOLUTION in files:
        try:
            Evolution().load_dict(json.loads(files[EVOLUTION].read_text(encoding="utf-8")))
        except (ValueError, TypeError, EvolutionError) as exc:
            raise ChipError(f"evolução inválida: {exc}") from exc
    if VOICE in files:
        from akemi.audio.voice_id import VoiceError, VoicePrint

        try:
            VoicePrint.from_dict(json.loads(files[VOICE].read_text(encoding="utf-8")))
        except (ValueError, VoiceError) as exc:
            raise ChipError(f"voz inválida: {exc}") from exc
    if LIFE in files:
        from akemi.brain.vida import Vida

        try:
            Vida().load_dict(json.loads(files[LIFE].read_text(encoding="utf-8")))
        except (ValueError, TypeError, KeyError) as exc:
            raise ChipError(f"vida inválida: {exc}") from exc
    if MEMORY in files:
        try:
            db = sqlite3.connect(f"file:{files[MEMORY]}?mode=ro", uri=True)
            try:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ChipError("memória corrompida")
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"episodes", "facts"} <= tables:
                    raise ChipError("memória sem as tabelas da Akemi")
            finally:
                db.close()
        except sqlite3.DatabaseError as exc:
            raise ChipError(f"memória inválida: {exc}") from exc


def open_chip(path: Path, dest: Path, *, key: str = "", allow_unsigned: bool = False) -> tuple[ChipInfo, dict[str, Path]]:
    """Abre, verifica e extrai o chip em ``dest``. Não instala nada."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ChipError(f"não é um chip válido: {exc}") from exc
    with zf:
        try:
            manifest = json.loads(zf.read(MANIFEST))
        except (KeyError, ValueError) as exc:
            raise ChipError("chip sem manifest.json válido") from exc
        if not isinstance(manifest, dict) or manifest.get("formato") != FORMAT:
            raise ChipError("não é um chip da Akemi")
        if manifest.get("versao") != VERSION:
            raise ChipError(f"versão de chip não suportada: {manifest.get('versao')}")
        listed = manifest.get("arquivos")
        if not isinstance(listed, dict) or PERSONA not in listed:
            raise ChipError("manifest sem a lista de arquivos")
        unknown = set(listed) - set(FILES)
        if unknown:
            raise ChipError(f"arquivos desconhecidos no chip: {', '.join(sorted(unknown))}")

        warnings: list[str] = []
        sig = manifest.get("assinatura")
        signed = isinstance(sig, str) and bool(sig)
        verified = False
        if key:
            if not signed:
                raise ChipError("chip sem assinatura, mas CHIP_KEY está definida")
            if not hmac.compare_digest(sig, _signature(manifest, key)):
                raise ChipError("assinatura não confere: chip alterado ou de outra chave")
            verified = True
        elif signed:
            warnings.append("chip assinado, mas sem CHIP_KEY aqui para conferir")
        elif not allow_unsigned:
            warnings.append("chip sem assinatura: defina CHIP_KEY para proteger seus chips")

        files: dict[str, Path] = {}
        for name, digest in listed.items():
            try:
                zinfo = zf.getinfo(name)
            except KeyError as exc:
                raise ChipError(f"falta {name} no chip") from exc
            limit = MAX_FILE_BYTES if name == MEMORY else MAX_SMALL_BYTES
            if zinfo.file_size > limit:
                raise ChipError(f"{name} grande demais")
            target = dest / name
            h = hashlib.sha256()
            written = 0
            with zf.open(zinfo) as src, target.open("wb") as out:
                for block in iter(lambda: src.read(1 << 20), b""):
                    written += len(block)
                    if written > limit:
                        raise ChipError(f"{name} grande demais")
                    h.update(block)
                    out.write(block)
            if not hmac.compare_digest(h.hexdigest(), str(digest)):
                raise ChipError(f"{name} corrompido (SHA-256 não confere)")
            files[name] = target
    _validate_contents(files)
    return _info(manifest, signed=signed, verified=verified, warnings=warnings), files


def inspect_chip(path: Path, *, key: str = "") -> ChipInfo:
    with tempfile.TemporaryDirectory() as tmp:
        info, _ = open_chip(path, Path(tmp), key=key)
    return info


# ============================================================== importar
def import_chip(path: Path, *, data_dir: Path, key: str = "") -> tuple[ChipInfo, Path | None]:
    """Instala o chip em ``data_dir``. Devolve (info, pasta do backup)."""
    data_dir = Path(data_dir)
    with tempfile.TemporaryDirectory() as tmp:
        info, files = open_chip(path, Path(tmp), key=key)
        data_dir.mkdir(parents=True, exist_ok=True)
        existing = [data_dir / n for n in FILES if (data_dir / n).exists()]
        backup = None
        if existing:
            backup = data_dir / time.strftime("backup-%Y%m%d-%H%M%S")
            backup.mkdir()
            for p in existing:
                shutil.copy2(p, backup / p.name)
        for n in FILES:
            target = data_dir / n
            if n in files:
                tmp_target = target.with_suffix(target.suffix + ".novo")
                shutil.copyfile(files[n], tmp_target)
                tmp_target.replace(target)
            elif target.exists() and n != PERSONA:
                target.unlink()  # chip sem esse arquivo: começa limpo (a cópia está no backup)
        for extra in ("memory.sqlite3-wal", "memory.sqlite3-shm"):
            (data_dir / extra).unlink(missing_ok=True)
    return info, backup


# ==================================================================== CLI
def main(argv: list[str] | None = None) -> int:
    from akemi.core.config import Settings

    parser = argparse.ArgumentParser(
        prog="akemi-chip", description="Exporta/importa a identidade da Akemi (persona, memória, estado, evolução)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_exp = sub.add_parser("exportar", help="grava a Akemi num chip")
    p_exp.add_argument("arquivo", type=Path)
    p_ins = sub.add_parser("inspecionar", help="mostra o que tem num chip, sem instalar")
    p_ins.add_argument("arquivo", type=Path)
    p_imp = sub.add_parser("importar", help="instala um chip (desligue a Akemi antes)")
    p_imp.add_argument("arquivo", type=Path)
    p_imp.add_argument("-s", "--sim", action="store_true", help="não pede confirmação")
    args = parser.parse_args(argv)

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    settings = Settings()
    try:
        if args.cmd == "exportar":
            info = export_chip(
                args.arquivo, data_dir=settings.data_dir, persona_file=settings.persona_file,
                owner=settings.owner_name, key=settings.chip_key,
            )
            print(info.describe())
            print(f"\nChip gravado em {args.arquivo}. Ele tem suas conversas: guarde com cuidado.")
        elif args.cmd == "inspecionar":
            print(inspect_chip(args.arquivo, key=settings.chip_key).describe())
        else:
            info = inspect_chip(args.arquivo, key=settings.chip_key)
            print(info.describe())
            if not args.sim:
                answer = input(
                    f"\nIsso substitui a Akemi atual em {settings.data_dir} (com backup). "
                    "A Akemi está desligada? Continuar? [s/N] "
                )
                if answer.strip().lower() not in ("s", "sim", "y"):
                    print("Cancelado.")
                    return 1
            info, backup = import_chip(args.arquivo, data_dir=settings.data_dir, key=settings.chip_key)
            print("Chip instalado." + (f" A Akemi anterior ficou em {backup}." if backup else ""))
    except ChipError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
