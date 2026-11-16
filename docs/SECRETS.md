# Gestão de Segredos e Credenciais

Este documento descreve as melhores práticas para gerenciar chaves de API, tokens e outros segredos na Akemi.

## 🔐 Princípios Básicos

1. **Nunca faça commit de arquivos `.env`**
2. Use `.env.example` como template
3. Gere senhas fortes para `AKEMI_TOKEN` e `CHIP_KEY`
4. Rotacione credenciais periodicamente

## 🪟 Windows Credential Manager

Para ambientes de desenvolvimento, você pode usar o Credential Manager do Windows:

```powershell
# Salvar uma chave
cmdkey /generic:AKEMI_NVIDIA_KEY /user:akemi /pass:sua-chave-aqui

# Ler da credencial (em Python)
import keyring
api_key = keyring.get_password("AKEMI_NVIDIA_KEY", "akemi")
```

### Integração com o código

No seu `.env`, use um placeholder:

```env
NVIDIA_API_KEY=USE_CREDENTIAL_MANAGER
```

E no código Python:

```python
import keyring
from akemi.config import settings

if settings.NVIDIA_API_KEY == "USE_CREDENTIAL_MANAGER":
    settings.NVIDIA_API_KEY = keyring.get_password("AKEMI_NVIDIA_KEY", "akemi")
```

## ☁️ Ambientes de Produção

### Azure Key Vault

Para produção no Azure:

```bash
pip install azure-keyvault-secrets azure-identity
```

```python
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential

credential = DefaultAzureCredential()
client = SecretClient(vault_url="https://seu-vault.vault.azure.net/", credential=credential)

nvidia_key = client.get_secret("nvidia-api-key").value
```

### HashiCorp Vault

Para infraestrutura on-premise ou multi-cloud:

```bash
pip install hvac
```

```python
import hvac

client = hvac.Client(url='http://127.0.0.1:8200')
client.token = 'seu-token'

secret = client.secrets.kv.v2.read_secret_version(path='akemi/keys')
nvidia_key = secret['data']['data']['NVIDIA_API_KEY']
```

## 🔑 Gerando Senhas Fortes

Para `AKEMI_TOKEN` e `CHIP_KEY`:

```python
import secrets

# Token para autenticação (24-32 caracteres)
print(secrets.token_urlsafe(32))

# Senha para backup (hexadecimal)
print(secrets.token_hex(32))
```

Nunca use senhas previsíveis ou curtas!

## 🐳 Docker Secrets

Para deployment com Docker Swarm:

```yaml
services:
  akemi:
    image: ghcr.io/mateus0790/akemi:latest
    secrets:
      - nvidia_api_key
      - akemi_token
    environment:
      NVIDIA_API_KEY_FILE: /run/secrets/nvidia_api_key
      AKEMI_TOKEN_FILE: /run/secrets/akemi_token

secrets:
  nvidia_api_key:
    external: true
  akemi_token:
    external: true
```

## 📋 Checklist de Segurança

- [ ] `.env` está no `.gitignore`
- [ ] Senhas têm no mínimo 24 caracteres
- [ ] Chaves de API são rotacionadas a cada 90 dias
- [ ] Credenciais de produção usam vault (Azure/HashiCorp)
- [ ] Logs não expõem chaves ou tokens
- [ ] Backups do `data/` são criptografados
- [ ] `AKEMI_TOKEN` é diferente em dev e produção

## 🚨 Se uma Chave Vazar

1. **Revogue imediatamente** no provedor (NVIDIA, GitHub, etc.)
2. Gere uma nova chave
3. Atualize todos os ambientes
4. Revise logs para detectar uso não autorizado
5. Considere reportar via [SECURITY.md](../SECURITY.md)

## 🔄 Rotação Automática

Exemplo de script para rotacionar o `AKEMI_TOKEN` mensalmente:

```python
import secrets
from pathlib import Path
import os

def rotate_token():
    env_file = Path(".env")
    if not env_file.exists():
        return
    
    new_token = secrets.token_urlsafe(32)
    content = env_file.read_text()
    
    # Backup
    backup = Path(f".env.backup.{datetime.now().strftime('%Y%m%d')}")
    backup.write_text(content)
    
    # Atualizar
    lines = []
    for line in content.splitlines():
        if line.startswith("AKEMI_TOKEN="):
            lines.append(f"AKEMI_TOKEN={new_token}")
            print(f"🔄 Novo token gerado. Atualize o servidor!")
        else:
            lines.append(line)
    
    env_file.write_text("\n".join(lines))

# Execute mensalmente via cron/Task Scheduler
rotate_token()
```

## 📚 Recursos

- [OWASP Secrets Management](https://owasp.org/www-community/vulnerabilities/Use_of_hard-coded_password)
- [12-Factor App: Config](https://12factor.net/config)
- [Azure Key Vault Best Practices](https://learn.microsoft.com/azure/key-vault/general/best-practices)
