#!/usr/bin/env python3
"""
Verifica dependências desatualizadas e vulnerabilidades conhecidas.

Uso:
    python scripts/check_dependencies.py
    python scripts/check_dependencies.py --update
"""

import argparse
import subprocess
import sys
from pathlib import Path


def check_outdated():
    """Lista dependências desatualizadas"""
    print("🔍 Verificando dependências desatualizadas...\n")
    result = subprocess.run(["pip", "list", "--outdated", "--format=columns"], capture_output=True, text=True)
    
    if result.stdout.strip():
        print(result.stdout)
    else:
        print("✓ Todas as dependências estão atualizadas!")


def check_vulnerabilities():
    """Verifica vulnerabilidades com pip-audit"""
    print("\n🛡️ Verificando vulnerabilidades conhecidas...\n")
    
    try:
        result = subprocess.run(["pip-audit", "--desc"], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✓ Nenhuma vulnerabilidade conhecida encontrada!")
        else:
            print(result.stdout)
            if "No known vulnerabilities found" in result.stdout:
                print("✓ Nenhuma vulnerabilidade conhecida encontrada!")
            else:
                print("\n⚠️ Vulnerabilidades encontradas! Considere atualizar as dependências.")
                return False
                
    except FileNotFoundError:
        print("⚠️ pip-audit não instalado. Instale com: pip install pip-audit")
    
    return True


def update_dependencies(dev: bool = False):
    """Atualiza dependências"""
    print("\n📦 Atualizando dependências...\n")
    
    extras = "[dev]" if dev else ""
    cmd = ["pip", "install", "--upgrade", f".{extras}"]
    
    subprocess.run(cmd, cwd=Path(__file__).parent.parent)
    print("\n✓ Dependências atualizadas!")


def main():
    parser = argparse.ArgumentParser(description="Verifica e atualiza dependências")
    parser.add_argument("--update", action="store_true", help="Atualizar dependências")
    parser.add_argument("--dev", action="store_true", help="Incluir dependências de dev")
    
    args = parser.parse_args()
    
    if args.update:
        update_dependencies(args.dev)
    else:
        check_outdated()
        vulnerabilities_ok = check_vulnerabilities()
        
        if not vulnerabilities_ok:
            sys.exit(1)


if __name__ == "__main__":
    main()
