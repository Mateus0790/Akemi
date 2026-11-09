"""Assinatura: a Akemi sabe se é a oficial, uma modificada ou código-fonte."""

import base64
import json

import pytest

from akemi import assinatura as sig
from akemi.assinatura import FONTE, MODIFICADA, OFICIAL, OUTRA, generate_keys, sign, verify


@pytest.fixture(scope="module")
def keys():
    return generate_keys()


def _package(root):
    (root / "brain").mkdir(parents=True)
    (root / "__init__.py").write_text("__version__ = '0.2.0'\n")
    (root / "brain" / "mind.py").write_text("def pensar():\n    return 'oi'\n")
    (root / "brain" / "persona.yaml").write_text("name: Akemi\n")
    return root


def _exe_folder(root):
    (root / "_internal").mkdir(parents=True)
    (root / "Akemi.exe").write_bytes(b"MZ fake exe")
    (root / "_internal" / "python312.dll").write_bytes(b"dll")
    (root / "_internal" / "base_library.zip").write_bytes(b"zip")
    return root


def test_official_package(tmp_path, keys):
    private, public = keys
    root = _package(tmp_path / "akemi")
    manifest = sign(root, private, commit="abc123")
    assert (root / "_assinatura.json").exists() and len(manifest["arquivos"]) == 3
    check = verify(root, keys=(public,))
    assert check.status == OFICIAL and check.official and check.commit == "abc123"
    assert check.fingerprint == manifest["impressao"]
    assert "oficial" in check.for_prompt()


def test_caches_and_other_files_do_not_count(tmp_path, keys):
    private, public = keys
    root = _package(tmp_path / "akemi")
    sign(root, private)
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
    (root / "anotacoes.txt").write_text("nada")
    assert verify(root, keys=(public,)).status == OFICIAL


@pytest.mark.parametrize("change,attr,name", [
    (lambda r: (r / "brain" / "mind.py").write_text("def pensar():\n    return 'hackeada'\n"),
     "changed", "brain/mind.py"),
    (lambda r: (r / "brain" / "persona.yaml").unlink(), "missing", "brain/persona.yaml"),
    (lambda r: (r / "brain" / "extra.py").write_text("import os\n"), "added", "brain/extra.py"),
])
def test_any_change_is_noticed(tmp_path, keys, change, attr, name):
    private, public = keys
    root = _package(tmp_path / "akemi")
    sign(root, private)
    change(root)
    check = verify(root, keys=(public,))
    assert check.status == MODIFICADA and getattr(check, attr) == [name]
    assert "modificad" in check.for_prompt() and "diga a verdade" in check.for_prompt()


def test_fork_with_own_key_is_not_official(tmp_path, keys):
    _, official = keys
    fork_private, _ = generate_keys()
    root = _package(tmp_path / "akemi")
    sign(root, fork_private)
    assert verify(root, keys=(official,)).status == OUTRA


def test_cannot_edit_the_signature_file(tmp_path, keys):
    private, public = keys
    root = _package(tmp_path / "akemi")
    sign(root, private)
    (root / "brain" / "mind.py").write_text("hackeada = True\n")
    path = root / "_assinatura.json"
    manifest = json.loads(path.read_text())
    manifest["arquivos"] = sig.hash_files(root)  # "atualiza" a lista para bater
    path.write_text(json.dumps(manifest))
    check = verify(root, keys=(public,))
    assert check.status == MODIFICADA and "adulterad" in check.detail


def test_cannot_claim_the_official_key(tmp_path, keys):
    _, official = keys
    fork_private, _ = generate_keys()
    root = _package(tmp_path / "akemi")
    sign(root, fork_private)
    path = root / "_assinatura.json"
    manifest = json.loads(path.read_text())
    manifest["chave"] = official  # diz que é a chave oficial, mas assinou com outra
    path.write_text(json.dumps(manifest))
    assert verify(root, keys=(official,)).status == MODIFICADA


def test_garbage_signature_file(tmp_path, keys):
    root = _package(tmp_path / "akemi")
    (root / "_assinatura.json").write_text("{nada")
    assert verify(root, keys=(keys[1],)).status == MODIFICADA


def test_source_without_signature(tmp_path, keys):
    root = _package(tmp_path / "akemi")
    check = verify(root, keys=(keys[1],))
    assert check.status == FONTE and check.fingerprint
    assert "desenvolvimento" in check.for_prompt()


def test_exe_folder_ignores_env_and_data(tmp_path, keys):
    private, public = keys
    root = _exe_folder(tmp_path / "Akemi")
    sign(root, private)
    assert (root / "assinatura.json").exists()
    (root / ".env").write_text("NVIDIA_API_KEY=x\n")
    (root / "data").mkdir()
    (root / "data" / "memory.sqlite3").write_bytes(b"sqlite")
    assert verify(root, keys=(public,)).status == OFICIAL
    (root / "_internal" / "python312.dll").write_bytes(b"dll trocada")
    check = verify(root, keys=(public,))
    assert check.status == MODIFICADA and check.changed == ["_internal/python312.dll"]


def test_signing_needs_a_valid_key(tmp_path):
    root = _package(tmp_path / "akemi")
    with pytest.raises(sig.SignatureError):
        sign(root, base64.b64encode(b"curta").decode())


def test_cli_sign_without_secret_is_a_no_op(tmp_path, monkeypatch, capsys):
    root = _package(tmp_path / "akemi")
    monkeypatch.delenv("AKEMI_SIGNING_KEY", raising=False)
    assert sig.main(["assinar", str(root)]) == 0
    assert not (root / "_assinatura.json").exists()
    assert "sem assinatura" in capsys.readouterr().out


def test_cli_sign_and_verify(tmp_path, monkeypatch, keys, capsys):
    private, public = keys
    root = _package(tmp_path / "akemi")
    monkeypatch.setenv("AKEMI_SIGNING_KEY", private)
    assert sig.main(["assinar", str(root)]) == 0
    monkeypatch.setattr("akemi.chave_oficial.PUBLIC_KEYS", (public,))
    assert sig.main(["verificar", str(root)]) == 0
    (root / "brain" / "mind.py").write_text("x = 1\n")
    assert sig.main(["verificar", str(root)]) == 1
    assert "mudaram: brain/mind.py" in capsys.readouterr().out


def test_this_checkout_is_source_code():
    check = sig.self_check(refresh=True)
    assert check.status == FONTE  # rodando do git, sem _assinatura.json


def test_health_report_has_the_code_status(tmp_path):
    from akemi.core.config import Settings
    from akemi.core.orchestrator import Akemi

    akemi = Akemi(Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path), persist=False,
                  desktop_actions=False)
    assert akemi.health_report()["codigo"]["status"] == FONTE
    assert "código-fonte" in akemi.describe_health()
