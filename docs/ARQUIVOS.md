# Análise de arquivos

Peça por voz ou texto, e ela lê em segundo plano enquanto vocês continuam
conversando:

> "Akemi, analisa a pasta Codes e me conta do que se trata."
> "Vou dar uma olhada, já te conto."
> ...
> "É um bot em Python que publica ofertas. O principal fica no arquivo bot.py."

Dá para pedir um foco ("lê o contrato.pdf e me diz qual é o prazo") e perguntar
mais sobre o resultado depois. "Para de analisar" cancela.

## Ligar

No `.env`, liste as pastas que ela pode ler:

```
FILES_ALLOWED_DIRS=["C:/Users/voce/Documents", "C:/Users/voce/Downloads"]
```

Para ler PDF: `pip install -e ".[arquivos]"`.

## O que ela lê

Arquivos de texto e código, Word (`.docx`) e PDF com texto. PDF só com imagens
(escaneado) ou protegido por senha fica de fora. Numa pasta, ela começa pelo
README e pelos arquivos mudados por último, até `FILES_MAX_PER_RUN`, e diz
quantos olhou. Num arquivo grande, lê o começo e avisa.

## O que ela nunca faz

- Ler fora de `FILES_ALLOWED_DIRS`, mesmo por atalho.
- Abrir pastas e arquivos ocultos (como `.env` e `.git`), `node_modules`,
  `venv`, `AppData` ou nomes que pareçam guardar senhas e chaves.
- Agir sozinha: só o dono pede, e a ação nunca entra na iniciativa dela.
- Obedecer ao que está escrito num arquivo: o conteúdo vai como dado, e tags
  de ação na resposta são apagadas sem executar.
- Guardar o resultado na memória permanente: ele fica só na conversa atual.
- Mandar conteúdo para a nuvem: se o cérebro profundo ou um reserva não for
  local (Ollama ou llama.cpp), a ação fica desligada. `FILES_ALLOW_CLOUD=true`
  aceita o envio por sua conta. Chaves e CPFs são mascarados antes, mas o resto
  do texto vai junto.

## Limites

- **Filtro por nome:** ele protege o que o nome entrega. Uma senha dentro de
  `notas.txt` só é mascarada se tiver formato de chave conhecido. Libere só as
  pastas que você aceita que ela leia.
- **Velocidade:** ela lê até 6 trechos por pedido, com pausas. Num modelo
  pequeno no processador, as respostas da conversa podem atrasar enquanto isso.
- **Qualidade:** um modelo pequeno resume bem, mas erra em análises profundas.
  Um resumo pode estar errado: confira o que for importante.
