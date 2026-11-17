# Painel desktop (Rust)

Uma janela para configurar **tudo** da Akemi sem abrir o `.env` no Bloco de Notas:
modelos de IA, vozes, pastas que ela pode ler, chaves, microfone, visão, memória,
secretária, MCP, personalidade e gatilhos. O código fica em `rust-core/akemi-painel`.

Ele não substitui o [painel no navegador](PAINEL.md): aquele muda algumas funções
**na hora**, com ela ligada. Este edita os arquivos de configuração e vale **na
próxima vez que ela ligar**.

## Como abrir

**Windows, sem instalar nada:** baixe o `akemi-painel.exe` em Actions →
"Painel (Rust)" → a execução mais recente → Artifacts → `akemi-painel-windows`.
Coloque o `.exe` na pasta da Akemi e abra.

**Compilando** (precisa do [Rust](https://rustup.rs)):

```powershell
cargo run --release --manifest-path rust-core/Cargo.toml -p akemi-painel
```

Ele acha a pasta da Akemi sozinho (procura `akemi/core/config.py` a partir de onde
você abriu e de onde o `.exe` está). Para apontar outra: `akemi-painel C:\Akemi`,
ou troque no campo "Pasta da Akemi", no topo.

## O que tem

| Aba | O que dá para mudar |
| --- | --- |
| Geral | seu nome, nome do corpo, chave do chip; um resumo e os avisos (chave faltando, voz sem modelo, pasta que não existe...) |
| Modelos de IA | provedor (NIM, OpenAI, Ollama, llama.cpp, Anthropic), modelo rápido e profundo, cérebros reserva em ordem, chaves e endereços, temperatura |
| Voz | voz principal e reserva; Fish Audio (chave, voz, modelo, velocidade, volume), ElevenLabs (chave, voz, modelo, ajustes da voz, velocidade, idioma), Kokoro (49 vozes), Piper (os `.onnx` em `data/voices`) |
| Ouvir | microfone, palavras que a chamam, reconhecer sua voz, áudio do PC, modelo e placa do Whisper, idioma |
| Visão | tela, monitor, modelo e provedor de visão, OCR, janelas que ela nunca lê |
| Pastas e arquivos | as pastas que ela pode ler (avisa se alguma não existe), se pode mandar arquivos para IA na nuvem |
| Memória | organizar a memória, busca por significado (embeddings), acompanhar sua vida |
| Comportamento | falas espontâneas, iniciativa e as ações que ela pode fazer sozinha, autoevolução, chat da live |
| Secretária nos chats | quando responder, grupos, quem ignorar, tokens do Telegram e Discord, Teams |
| Apps e integrações | MCP (com o `mcp.json` para editar), OBS, Modo Casca, chamadas |
| Oficina | token do GitHub, repositório, limites, modelo |
| Rede e painel | AKEMI_TOKEN, núcleo e corpo, painel web, registros |
| Avatar | tamanho, aparência, cochilo |
| Personalidade / Gatilhos | o `persona.yaml` e o `triggers.yaml`, com conferência do formato antes de salvar |

Pouco usados ficam em **Avançado**, no fim de cada aba. A busca no topo acha qualquer
configuração pelo nome, pela explicação ou pela variável (`FISH_SPEED`).

**Buscar modelos e vozes:** o botão ↻ ao lado de um campo de modelo pergunta ao provedor
quais modelos ele tem (`/models`) e o ⏷ mostra a lista. Funciona com a chave e o endereço
que estão na tela, mesmo antes de salvar. No campo da voz da Fish Audio, ele traz as suas
vozes.

## Como ele salva

- **`.env`**: troca só as linhas que você mudou, no mesmo lugar. Comentários, linhas em
  branco e a ordem ficam como estavam. Chaves novas vão para o fim. Na primeira gravação
  ele guarda uma cópia em `.env.bak`. Se ainda não existe `.env`, ele nasce do
  `.env.example`.
- **`data/painel.json`**: o painel do navegador grava por cima do `.env`. Se uma
  configuração está lá, a página mostra "vem do painel web" e, ao salvar, ele atualiza os
  dois para não ficarem diferentes.
- **Variáveis de ambiente** do sistema valem por cima do `.env`; o painel avisa quando
  alguma está definida.
- Nada é gravado com erro: número fora do limite, provedor que não existe, YAML ou JSON
  quebrado. A aba com erro fica vermelha e o Salvar diz o que falta.
- Fechar com mudanças sem salvar pergunta antes.

Atalho: **Ctrl+S** salva.

## Segurança

As chaves aparecem escondidas (botão "mostrar"). Elas ficam no `.env`, como sempre: não
mande esse arquivo nem o `.env.bak` para ninguém (os dois estão no `.gitignore`). O painel
só fala com a internet quando você clica em ↻, e só com o provedor daquele campo.

## Configuração nova no Python

A lista do painel fica em `rust-core/akemi-painel/src/schema.rs`. Um teste
(`cargo test -p akemi-painel`) lê o `akemi/core/config.py` e falha se alguma
configuração de lá não estiver no painel, então ninguém esquece.
