# Assinatura: Akemi oficial ou modificada

Cada build oficial (o `Akemi.exe` das Releases e a imagem `ghcr.io/mateus0790/akemi`)
sai **assinado** pelo CI com uma chave Ed25519 que só o dono do projeto tem. A
assinatura guarda a impressão digital (SHA-256) de cada arquivo do código.

Ao ligar, a Akemi confere a si mesma e sabe o que é:

| Resultado | Quer dizer |
| --- | --- |
| **oficial** | assinada com a chave oficial e nenhum arquivo mudou |
| **modificada** | assinada, mas algum arquivo mudou, sumiu ou apareceu depois do build (ou a assinatura foi adulterada) |
| **assinada por outra chave** | um fork que assina os próprios builds: não é a oficial |
| **código-fonte** | rodando do git, sem assinatura. Normal para quem desenvolve |

Onde ver:

- `/assinatura` ou `/saude` no console
- `akemi-assinatura` (ou `Akemi.exe assinatura`). Para conferir outra cópia: `akemi-assinatura verificar <pasta>`
- `GET /saude` no servidor (campo `codigo`)
- pergunte a ela: "você é a Akemi oficial?". Ela sabe e conta a verdade

No `.exe`, o `.env`, a pasta `data/` e o que você colocar ao lado dele **não**
contam: só o `Akemi.exe` e a pasta `_internal`.

## O que isso garante (e o que não garante)

O código é aberto, então uma cópia modificada pode apagar essa checagem ou mentir
sobre o resultado. O que ela **não** consegue é criar uma assinatura válida da
chave oficial. Então:

- a sua Akemi oficial percebe se algum arquivo dela foi trocado depois do build
  (por engano, por um programa, por alguém)
- para conferir uma cópia em que você **não** confia, rode o `akemi-assinatura
  verificar <pasta dela>` de uma Akemi em que você confia: a cópia não tem como enganar
- quem fizer um fork pode assinar com a chave dele, e aparece como "outra chave"

## Configurar (uma vez)

1. No seu PC, dentro do repositório:

   ```powershell
   akemi-assinatura gerar-chave
   ```

   Ele grava a chave **pública** em `akemi/chave_oficial.py` e mostra a chave **privada**.
2. Cole a chave privada no GitHub: *Settings → Secrets and variables → Actions →
   New repository secret*, com o nome `AKEMI_SIGNING_KEY`. Não salve em arquivo nem mande para ninguém.
3. Faça commit do `akemi/chave_oficial.py` e mande para a main.

Daí em diante, os builds da main e das tags `v*` saem assinados. Sem o secret, o
CI continua funcionando e os builds saem como "código-fonte".

Para trocar de chave sem invalidar as versões antigas: gere outra (ela entra junto
na lista `PUBLIC_KEYS`), troque o secret e, quando quiser, tire a antiga da lista.
