# Oficina: ela abre PR no próprio código

A Akemi pode mexer no próprio código-fonte e abrir um Pull Request no GitHub
para você revisar. Exemplo:

> "Akemi, cria uma habilidade que diz a previsão do tempo e abre um PR."
> "Abro um PR no GitHub para isso: criar uma ação que diz a previsão do tempo? Diz sim ou não."
> "Sim."
> "Beleza, vou mexer no meu código. Isso leva uns minutos..."
> ...
> "Abri um PR: ação de previsão do tempo. Tá no GitHub esperando você revisar."

## Como ela trabalha

1. Baixa a `main` do GitHub numa **cópia isolada** (git worktree em
   `data/oficina/`). A sua cópia de trabalho e a Akemi que está rodando não
   são tocadas.
2. Escolhe os arquivos que precisa ler e escreve a mudança.
3. Roda **todos os testes** do projeto na cópia isolada. Se falharem, ela lê
   o erro e tenta de novo (até 2 vezes). Se não passarem, ela desiste e conta.
4. Confere o diff atrás de chaves, tokens, e-mails e CPFs. Se achar, não publica.
5. Envia para uma branch `akemi/...` e abre o PR como **rascunho**.
6. Apaga a cópia isolada e te conta o resultado.

## O que ela nunca faz

- **Juntar o PR (merge).** Quem decide é sempre você, no GitHub.
- Mexer na `main` direto, ou abrir mais de `OFICINA_PER_DAY` PRs (padrão 2)
  em 24 horas.
- Abrir PR sem você pedir. A ação `abrir_pr` sempre pede "sim" (e com a voz
  cadastrada, o "sim" tem que ser seu) e nunca entra na iniciativa dela.
- Mexer nos **arquivos protegidos**: a segurança dela (moderação, permissões
  das ações, filtros de lealdade, reconhecimento de voz, autenticação do
  núcleo e do celular, chip), a persona, a própria oficina, a pasta
  `.github` (um workflow alterado poderia vazar segredos do CI), `.env`,
  `data/` e o Docker. Lista completa em `akemi/oficina/patch.py`.
- Usar sua memória, a tela ou o chat: para o modelo vão só o seu pedido e o
  código do repositório.

## Ligar

1. Crie um token no GitHub, com o mínimo de permissão:
   **Settings → Developer settings → Personal access tokens → Fine-grained
   tokens → Generate new token**
   - Repository access: **Only select repositories → Akemi**
   - Permissions: **Contents: Read and write** e **Pull requests: Read and write**
   - Validade curta (90 dias, por exemplo)
2. Proteja a `main` (recomendado): **Akemi → Settings → Branches → Add rule**
   para `main` com "Require a pull request before merging". Assim nem o token
   consegue empurrar direto na `main`, mesmo se algo der errado.
3. No `.env` do PC onde o repositório está clonado:

```
OFICINA_ENABLED=true
GITHUB_TOKEN=github_pat_...
GITHUB_REPO=Mateus0790/Akemi
# OFICINA_REPO_DIR=.         (pasta do repositório, se rodar a Akemi de outro lugar)
# OFICINA_MODEL=             (vazio = DEEP_MODEL; quanto melhor o modelo, melhor o código)
# OFICINA_PER_DAY=2
```

Precisa do `git` instalado. No Docker do ZimaOS ela não funciona (lá não tem
o repositório nem o git): use no PC.

## Usar

- Por voz ou texto: "abre um PR para ...". Ela pede "sim".
- No console: `/pr <pedido>` começa direto (digitar no terminal já é você
  pedindo) e `/prs` mostra os PRs que ela abriu ou tentou abrir.

## Limites (leia antes)

- **A qualidade depende do modelo.** Com o `gpt-oss-20b` ou um modelo local
  pequeno, ela acerta mudanças pequenas e bem explicadas, como "adiciona uma
  ação que diz o dia da semana". Coisa grande (uma integração inteira)
  provavelmente vai falhar nos testes. Um modelo maior em `OFICINA_MODEL` ajuda.
- **Os testes rodam código que ela escreveu, no seu PC.** As suas chaves e
  tokens são tirados do ambiente antes, e o código vem só do seu pedido, não
  de conteúdo de terceiros. Mesmo assim, é código gerado por IA rodando na
  sua máquina.
- **Revise o PR antes do merge**, sempre. Passar nos testes não quer dizer
  que está certo.
- O PR é público se o repositório for público.
- Ela ainda não procura erros nos logs sozinha para propor correções.
  Por enquanto, só trabalha no que você pede.
