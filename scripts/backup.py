#!/usr/bin/env python3
"""
Script de backup automático da Akemi.
Faz backup incremental do diretório data/ com compressão e criptografia opcional.

Uso:
    python scripts/backup.py
    python scripts/backup.py --encrypt --password sua-senha
    python scripts/backup.py --restore backup_20261116.tar.gz
"""

import argparse
import shutil
import tarfile
from datetime import datetime
from pathlib import Path
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def create_backup(data_dir: Path, backup_dir: Path, encrypt: bool = False, password: str = "") -> Path:
    """Cria backup do diretório data/"""
    if not data_dir.exists():
        logger.error(f"Diretório {data_dir} não existe!")
        sys.exit(1)

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"akemi_backup_{timestamp}.tar.gz"
    backup_path = backup_dir / backup_name

    logger.info(f"Criando backup: {backup_path}")

    try:
        with tarfile.open(backup_path, "w:gz") as tar:
            tar.add(data_dir, arcname="data")

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        logger.info(f"✓ Backup criado com sucesso! Tamanho: {size_mb:.2f} MB")

        if encrypt and password:
            encrypted_path = encrypt_backup(backup_path, password)
            backup_path.unlink()  # Remove o não-criptografado
            logger.info(f"✓ Backup criptografado: {encrypted_path}")
            return encrypted_path

        return backup_path

    except Exception as e:
        logger.error(f"Erro ao criar backup: {e}")
        sys.exit(1)


def encrypt_backup(backup_path: Path, password: str) -> Path:
    """Criptografa o backup usando cryptography"""
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import base64
        import os

        # Gerar chave a partir da senha
        salt = os.urandom(16)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        fernet = Fernet(key)

        # Ler e criptografar
        data = backup_path.read_bytes()
        encrypted_data = salt + fernet.encrypt(data)

        # Salvar criptografado
        encrypted_path = backup_path.with_suffix(backup_path.suffix + ".enc")
        encrypted_path.write_bytes(encrypted_data)

        return encrypted_path

    except ImportError:
        logger.error("pip install cryptography para usar criptografia!")
        sys.exit(1)


def restore_backup(backup_path: Path, restore_dir: Path, password: str = None):
    """Restaura backup"""
    if not backup_path.exists():
        logger.error(f"Backup {backup_path} não encontrado!")
        sys.exit(1)

    logger.info(f"Restaurando backup: {backup_path}")

    try:
        # Descriptografar se necessário
        if backup_path.suffix == ".enc":
            if not password:
                logger.error("Backup criptografado requer --password!")
                sys.exit(1)
            backup_path = decrypt_backup(backup_path, password)

        # Extrair
        with tarfile.open(backup_path, "r:gz") as tar:
            tar.extractall(restore_dir)

        logger.info(f"✓ Backup restaurado em: {restore_dir}")

    except Exception as e:
        logger.error(f"Erro ao restaurar backup: {e}")
        sys.exit(1)


def decrypt_backup(encrypted_path: Path, password: str) -> Path:
    """Descriptografa backup"""
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import base64

        encrypted_data = encrypted_path.read_bytes()
        salt = encrypted_data[:16]
        encrypted_data = encrypted_data[16:]

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        fernet = Fernet(key)

        decrypted_data = fernet.decrypt(encrypted_data)

        # Salvar temporário
        temp_path = encrypted_path.with_suffix(".tar.gz")
        temp_path.write_bytes(decrypted_data)

        return temp_path

    except Exception as e:
        logger.error(f"Erro ao descriptografar (senha incorreta?): {e}")
        sys.exit(1)


def cleanup_old_backups(backup_dir: Path, keep_last: int = 7):
    """Remove backups antigos, mantendo apenas os N mais recentes"""
    backups = sorted(backup_dir.glob("akemi_backup_*.tar.gz*"), key=lambda p: p.stat().st_mtime, reverse=True)

    if len(backups) <= keep_last:
        return

    for old_backup in backups[keep_last:]:
        logger.info(f"Removendo backup antigo: {old_backup.name}")
        old_backup.unlink()


def main():
    parser = argparse.ArgumentParser(description="Backup e restore da Akemi")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Diretório a fazer backup")
    parser.add_argument("--backup-dir", type=Path, default=Path("backups"), help="Onde salvar backups")
    parser.add_argument("--encrypt", action="store_true", help="Criptografar backup")
    parser.add_argument("--password", help="Senha para criptografia/descriptografia")
    parser.add_argument("--restore", type=Path, help="Restaurar de um backup")
    parser.add_argument("--restore-dir", type=Path, default=Path("."), help="Onde restaurar")
    parser.add_argument("--keep", type=int, default=7, help="Quantos backups manter (padrão: 7)")

    args = parser.parse_args()

    if args.restore:
        restore_backup(args.restore, args.restore_dir, args.password)
    else:
        backup_path = create_backup(args.data_dir, args.backup_dir, args.encrypt, args.password)
        cleanup_old_backups(args.backup_dir, args.keep)
        logger.info(f"✓ Backup completo: {backup_path}")


if __name__ == "__main__":
    main()
