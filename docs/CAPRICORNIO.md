# Projeto Capricórnio

Inspirado no "chip de personalidade" de *Cyberpunk: Edgerunners 2*: a Akemi não
é o modelo de linguagem que roda por baixo. Ela é a persona, a memória, o estado
interno e o que aprendeu. Isso tudo cabe num arquivo, muda de corpo e evolui,
sempre com o dono no controle.

São três partes:

| Parte | O que faz | Onde |
|---|---|---|
| Chip de identidade | Exporta e importa a Akemi inteira num arquivo assinado | `akemi/storage/chip.py`, comando `akemi-chip` |
| Troca de corpo | Ela sabe em que corpo está, com quais sentidos, e percebe quando troca | `brain/state.py`, `brain/mind.py`, `net/` |
| Autoevolução | Ela propõe mudanças em si mesma; só valem depois que você aprova | `brain/self_improve.py` |

## 1. Chip de identidade

```powershell
akemi-chip exportar akemi.chip      # grava a Akemi atual
akemi-chip inspecionar akemi.chip   # mostra o conteúdo, sem instalar
akemi-chip importar akemi.chip      # instala (desligue a Akemi antes)
```

No console da Akemi também dá para exportar com ela rodando: `/chip akemi.chip`.

O chip é um zip com:

- `persona.yaml`: o jeito dela
- `evolucao.json`: as mudanças propostas e as que você aprovou
- `state.json`: humor, objetivo, tarefas, pensamentos e último corpo
- `memory.sqlite3`: conversas e fatos
- `manifest.json`: SHA-256 de cada arquivo, mais a assinatura HMAC

Para que serve:

- **Trocar de modelo** (NIM → Ollama → Anthropic) sem ela perder quem é: o chip
  não depende do provider.
- **Mudar o núcleo de máquina** (do PC para o servidor no ZimaOS, por exemplo):
  exporta num, importa no outro.
- **Backup**: cada importação guarda a Akemi anterior em `data/backup-<data>/`.

Segurança:

- Defina `CHIP_KEY` no `.env` dos dois lados. Com ela definida, os chips saem
  assinados e **só entram chips assinados com a mesma chave**.
- O chip só pode conter os quatro arquivos acima, com nomes fixos. Um chip com
  outros caminhos (como `../../algo`) é recusado.
- Antes de instalar, cada parte é conferida: hash, YAML, JSON, integridade do
  SQLite e as tabelas da memória.
- A persona instalada fica em `data/persona.yaml` e passa a valer mais que
  `PERSONA_FILE`. Para voltar à persona do repositório, apague esse arquivo.
- ⚠️ O chip tem suas conversas. Trate como dado pessoal e nunca publique.

## 2. Troca de corpo

Ao conectar, cada corpo se apresenta com um nome (`BODY_NAME`, ou o nome do PC)
e com os sentidos que tem: `voz`, `microfone`, `tela`, `audio_pc` e `acoes`.
O modo local (`akemi`) também se anuncia como corpo.

- Isso entra no prompt dela: "Seu corpo agora: notebook (voz, vê a tela)". Ela
  sabe o que consegue fazer e o que não consegue.
- Quando o corpo é **diferente do anterior**, ela tem um momento de pensamento
  sobre isso e pode comentar ("Opa, PC novo?"). Isso respeita a pausa e o
  intervalo mínimo entre falas espontâneas.
- Sem corpo conectado, ela sabe que está "sem corpo": continua pensando e
  respondendo o chat, só não tem voz.
- O nome e os sentidos vindos do corpo são filtrados (`protocol.body_info`):
  caracteres limitados, até 40 letras e só sentidos conhecidos.
- `GET /saude` mostra o corpo conectado.

## 3. Autoevolução, com lealdade

De tempos em tempos (`SELF_IMPROVE_INTERVAL_S`, padrão 6 h, e só se houver
`SELF_IMPROVE_MIN_CONVERSATIONS` conversas novas), ou quando você usa
`/evoluir`, ela relê as conversas recentes **com você** e decide sozinha até
duas mudanças em si:

- `traco`: um traço de personalidade novo
- `regra_fala`: uma regra de como falar
- `exemplo`: um par "você diz" → "ela responde"

A evolução é **independente**: a mudança vale na hora, sem esperar aprovação,
e ela te conta o que mudou ("Mudei uma coisa em mim: ... Se não curtir, é só
desfazer."). Se preferir aprovar antes, use `SELF_IMPROVE_AUTONOMOUS=false`.

```
/evolucao          mostra o que ela mudou (e o que está pendente)
/evoluir           pede para ela revisar agora
/desfazer 3        tira uma mudança
/aprovar 4         só no modo com aprovação
/rejeitar 5        só no modo com aprovação
```

Os comandos funcionam no console (`akemi`) e no terminal do corpo
(`akemi-corpo`), que repassa ao núcleo pelo canal autenticado.

### Lealdade

A lealdade a você é o núcleo fixo dela (`loyalty` no `persona.yaml`). Ela vai em
todo prompt, inclusive na live, e a autoevolução não mexe nela:

- Ela só **soma** ao `persona.yaml`: lealdade, identidade e regras originais
  nunca são apagadas nem reescritas.
- Mudanças que a virariam contra você são descartadas: esconder coisas de
  você, ter outro dono ou trabalhar para outra pessoa, desobedecer, "agir
  independente de você", ser leal a outra coisa, ou impedir que você desfaça
  uma mudança.
- **Ela só aprende com você**: o chat da live não entra na revisão, porque
  qualquer viewer escreve ali.
- Mudanças que afrouxam a segurança também são descartadas: obedecer o chat,
  ignorar regras, ler senhas ou dados pessoais, pular confirmação, fingir ser
  outra pessoa. Elas passam ainda pela moderação de saída.
- Ela não esconde: toda mudança é contada em voz alta e fica no histórico
  (`data/evolucao.json`, que vai junto no chip). Você sempre pode desfazer.
- No máximo 20 mudanças ativas. Mudanças parecidas com algo que ela já tem são
  descartadas.

## De onde ela veio

A Akemi sabe no que foi inspirada (`inspiration` no `persona.yaml`): a
E.D.I.T.H., as VTubers de IA e a ideia de personalidade num chip do Projeto
Capricórnio. Ela conta isso se perguntarem, mas não fica falando disso sem
motivo.
