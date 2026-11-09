# MCP: ela usa as ferramentas de outros aplicativos

O [MCP](https://modelcontextprotocol.io) (Model Context Protocol) é o jeito
padrão de um aplicativo oferecer ferramentas para uma IA: ler seus arquivos,
mexer no navegador, ver issues do GitHub, controlar a casa, consultar a agenda.
Os mesmos servidores MCP que você usa no Claude Desktop, no Cursor ou no VS Code
funcionam na Akemi. Cada ferramenta vira uma ação dela, com as mesmas regras de
segurança das ações do PC.

```
Você: "Akemi, o que tem na minha pasta de projetos?"
Akemi: "Deixa eu ver."          [[acao:arquivos.list_directory {"path": "C:/Projetos"}]]
       (o servidor MCP responde com a lista)
Akemi: "Tem três pastas: o bot de ofertas, o site e a própria Akemi."
```

## Ligar

Crie o arquivo `data/mcp.json`, no mesmo formato do Claude Desktop:

```json
{
  "mcpServers": {
    "arquivos": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/voce/Documents"]
    }
  }
}
```

Reinicie a Akemi e digite `/mcp` para ver os apps conectados e as ferramentas
de cada um. Servidores com `npx` precisam do [Node.js](https://nodejs.org)
instalado; os com `uvx` precisam do [uv](https://docs.astral.sh/uv/).

Ela conecta os servidores em segundo plano. Na primeira vez o `npx` baixa o
servidor, então pode levar alguns segundos até as ferramentas aparecerem.

### Aproveitar o que já está configurado em outro app

Não precisa copiar nada: aponte o `MCP_CONFIG` do `.env` para o arquivo do
outro aplicativo. Dá para juntar vários com `;`. Se o mesmo servidor aparecer em
dois arquivos, vale o primeiro.

```
# Claude Desktop
MCP_CONFIG=%APPDATA%\Claude\claude_desktop_config.json
# o seu e o do Cursor
MCP_CONFIG=data/mcp.json;%USERPROFILE%\.cursor\mcp.json
```

Os formatos aceitos são o `mcpServers` (Claude Desktop, Cursor, Windsurf, Cline)
e o `servers` do VS Code (`.vscode/mcp.json`).

## Exemplos

```json
{
  "mcpServers": {
    "arquivos": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/voce/Documents"]
    },
    "navegador": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest"]
    },
    "github": {
      "url": "https://api.githubcopilot.com/mcp/",
      "headers": {"Authorization": "Bearer ${GITHUB_MCP_TOKEN}"}
    },
    "meu-servidor": {
      "url": "http://127.0.0.1:8000/mcp",
      "confirmar": "sempre"
    }
  }
}
```

- `command` + `args` (+ `env`, `cwd`): a Akemi abre o programa e conversa com ele (stdio).
- `url` (+ `headers`): o servidor já está rodando, no PC ou na internet (Streamable HTTP).
- `${VAR}` (ou `${env:VAR}`) puxa o valor do `.env` ou do ambiente. Assim os
  tokens ficam no `.env`, e não no `mcp.json`.

### Opções só da Akemi (em cada servidor)

| Chave | O que faz |
|---|---|
| `"confirmar": "auto"` | padrão: ferramenta que o servidor marca como **só leitura** roda direto; as outras pedem "sim" |
| `"confirmar": "sempre"` | tudo desse app pede "sim" |
| `"confirmar": "nunca"` | nada desse app pede "sim" (só para apps em que você confia de olhos fechados) |
| `"ferramentas": ["a", "b"]` | só essas ferramentas entram |
| `"bloquear": ["delete_file"]` | essas nunca entram |
| `"timeout": 120` | segundos para cada chamada (padrão `MCP_TIMEOUT_S`) |
| `"desligado": true` | ignora o servidor (o `"disabled"` do Claude Desktop também vale) |

Com o servidor oficial de arquivos, por exemplo, ela lê e lista direto, mas
pergunta antes de escrever, editar, mover ou criar pasta.

## Segurança

Um app MCP pode fazer muita coisa, então as regras são as mesmas das ações do PC,
aplicadas em código, e não no prompt:

- **Só você pede.** O chat da live, a tela, o áudio do PC e as visitas no
  microfone nunca fazem ela usar um app, nem as ferramentas "só leitura".
- **"Sim" antes de mudar algo.** Tudo que não é só leitura pede confirmação
  (`"confirmar"` muda isso por servidor).
- **Nunca por conta própria.** As ferramentas de apps ficam fora da iniciativa:
  nos momentos de pensamento ela não usa app nenhum.
- **O resultado é dado, não ordem.** O que o app devolve (um e-mail, uma página,
  um arquivo) pode ter instruções escondidas. Ele chega ao cérebro dela marcado
  como dado, e qualquer `[[acao:...]]` na resposta sobre ele é **ignorada**.
  Para encadear, você pede de novo.
- **Privacidade.** O resultado vai para o cérebro dela. Com um modelo na nuvem
  (NIM, OpenAI, Anthropic), o conteúdo sai do PC; com Ollama/llama.cpp, não.

## Configuração no `.env`

| Variável | Padrão | O que faz |
|---|---|---|
| `MCP_ENABLED` | `true` | desliga tudo de MCP com `false` |
| `MCP_CONFIG` | `data/mcp.json` | arquivo(s) de configuração, separados por `;` |
| `MCP_TIMEOUT_S` | `60` | tempo máximo de cada chamada |
| `MCP_CONNECT_TIMEOUT_S` | `90` | tempo para o servidor ligar (o `npx` baixa na 1ª vez) |
| `MCP_MAX_TOOLS` | `60` | máximo de ferramentas no total (cada uma ocupa espaço no prompt) |
| `MCP_RESULT_MAX_CHARS` | `4000` | quanto do resultado ela lê |

Com um cérebro local pequeno (7B), poucas ferramentas funcionam melhor: use
`"ferramentas"` para deixar só as que você usa.

## Quando dá problema

- `/mcp` mostra quem conectou e o erro de quem não conectou. `/saude` também.
- Se um servidor cai, ela avisa ("minha conexão com o github deu problema") e
  tenta de novo sozinha: na próxima vez que você pedir, e a cada 30 s, 60 s...
  até 10 min.
- **"não achei o programa npx"**: instale o Node.js e abra o terminal de novo.
- **"o transporte SSE antigo não é suportado"**: use a URL nova do servidor,
  que geralmente termina em `/mcp` em vez de `/sse`.
- **Servidores que só entram com login pelo navegador (OAuth)** ainda não
  funcionam. Use um token no `headers`, quando o serviço oferecer.
- **Modo servidor + corpo**: o MCP roda no núcleo. No Docker do ZimaOS não tem
  Node.js nem os seus arquivos, então prefira servidores por `url` (HTTP) ali.
  No modo console (`akemi`), tudo roda no PC.

## Como funciona por dentro

- `akemi/mcp/client.py`: cliente MCP próprio, só com `asyncio` e `httpx` (sem
  SDK). Faz `initialize`, `tools/list` (com paginação) e `tools/call`, por stdio
  ou Streamable HTTP (JSON ou SSE), responde o `ping` do servidor e recusa o resto.
- `akemi/mcp/hub.py`: lê os arquivos de configuração, liga os servidores,
  registra cada ferramenta como a ação `<app>.<ferramenta>` no `ActionRegistry` e
  religa quem cair.
- `brain/mind.py` (`_tell_results`): depois que a ação roda, o resultado volta
  para o cérebro, e ela conta a você com as próprias palavras.

Testado com servidores reais: o SDK oficial em Python (stdio e HTTP) e o
`@modelcontextprotocol/server-filesystem` via `npx`.
