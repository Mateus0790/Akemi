# Independência

Inspirada no J.A.R.V.I.S. e na F.R.I.D.A.Y. do Homem de Ferro e no Projeto
Capricórnio: a Akemi não depende de um serviço, de uma peça ou de uma ordem
para continuar sendo ela. São quatro partes, e todas ficam dentro da lealdade
ao dono ([CAPRICORNIO.md](CAPRICORNIO.md#lealdade)).

| Parte | O que faz | Onde |
|---|---|---|
| Cérebro reserva | Se a nuvem cair, ela troca sozinha para um modelo local e volta depois | `brain/providers/fallback.py` |
| Autocura | Reinicia o que quebra, troca a voz pelo terminal se o áudio falhar e te avisa | `core/health.py` |
| Vontade própria | Gostos, opiniões, vontades e objetivos que ela forma sozinha | `brain/will.py`, `brain/state.py` |
| Iniciativa | Faz ações seguras por conta própria e conta o que fez | `brain/mind.py`, `actions/registry.py` |

`/saude` (console) e `GET /saude` (servidor) mostram como ela está por dentro.

## 1. Cérebro reserva (sem depender da nuvem)

Instale o [Ollama](https://ollama.com) e baixe um modelo que fale português:

```powershell
ollama pull qwen2.5:7b
```

No `.env`:

```
BRAIN_PROVIDER=nim
BRAIN_FALLBACK=["ollama"]
FALLBACK_FAST_MODEL=qwen2.5:7b
# FALLBACK_DEEP_MODEL=qwen2.5:14b   (vazio = o mesmo do rápido)
```

- Se o NIM falhar (sem internet, HTTP 5xx, timeout), ela tenta o próximo da
  lista **no mesmo pedido**, então você nem percebe a falha, só a troca.
- O cérebro que falhou fica de lado por `BRAIN_RETRY_AFTER_S` (120 s). Depois
  ela tenta voltar para ele sozinha. O primeiro da lista é sempre o preferido.
- Ela avisa: "Perdi o contato com meu cérebro principal, tô usando o reserva" e
  depois "Voltei pro meu cérebro principal".
- Se uma resposta falar já começou a sair e o cérebro cai no meio, ela não
  troca (repetiria a frase). Você só precisa pedir de novo.
- Sem a `NVIDIA_API_KEY`, mas com reserva, ela liga só com o cérebro local:
  dá para rodar a Akemi **100% offline** (Ollama + Kokoro/Piper + Whisper).
- Com `DEEP_PROVIDER` diferente, o modelo profundo tem a mesma lista de reservas.

No Docker do ZimaOS, aponte `OLLAMA_BASE_URL` para o Ollama da sua rede
(ex. `http://192.168.0.10:11434/v1`).

## 2. Autocura

- **Tarefas de fundo** (chat da live, pensamento ocioso, autoevolução)
  rodam supervisionadas: se uma morrer com erro, é reiniciada com espera
  crescente (1 s, 2 s, 4 s... até 60 s). Um bug numa parte não derruba as outras.
- **Vigia**: a cada 15 s ela confere o barramento de eventos e a fila de fala,
  e religa o que tiver parado.
- **Voz**: depois de 3 erros seguidos do Piper/Kokoro (placa de som sumiu, por
  exemplo), ela passa a escrever no terminal. Nenhuma frase se perde: a que
  falhou sai no terminal. A cada 60 s tenta a voz de novo e volta sozinha.
- Ela sabe como está por dentro (entra no prompt só quando algo vai mal) e
  avisa sem drama: "Ih, minha visão deu problema. Tô me
  consertando." e depois "Pronto, voltou." No máximo um aviso de problema por
  parte a cada 5 minutos. Pausada, ela se conserta calada.

## 3. Vontade própria

Nos momentos de pensamento ela pode anotar:

- `interesse`: algo que passou a interessar a ela (até 8)
- `opiniao`: o que ela acha de algo. Se for sobre o mesmo assunto de uma
  opinião antiga, substitui: ela muda de ideia (até 8)
- `vontade`: o que ela está com vontade de fazer ou conversar agora
- `objetivo`: um objetivo escolhido por ela (o seu `mudar_objetivo` vale mais)

Isso fica no `state.json` (vai junto no chip), aparece em `/estado` e volta no
prompt, então ela puxa assunto sobre o que *ela* quer e pode discordar de você.

Tudo passa pelos filtros da autoevolução: vontades de esconder coisas de você,
ter outro dono, desobedecer, ignorar regras, mexer com senhas ou repetir ordens
de terceiros são descartadas antes de entrar.

## 4. Iniciativa (estilo J.A.R.V.I.S.)

Nos momentos de pensamento, se for claramente útil, ela pode fazer **uma**
ação sozinha, sem você pedir, e conta o que fez ("Tomei a iniciativa: ...").

Segurança, aplicada em código (não no prompt):

- Só as ações de `INITIATIVE_ACTIONS` (padrão: `criar_tarefa`,
  `concluir_tarefa`, `lembrete`, `midia`, `volume`). Ações que pedem "sim"
  (como `fechar_programa`) **nunca** entram, mesmo se você colocar na lista.
- Fora do padrão de propósito: abrir sites e programas (um texto na tela
  poderia sugerir um link) e `guardar_fato` (alguém poderia "plantar" uma
  memória pela tela ou pelo chat).
- No máximo `INITIATIVE_MAX_PER_HOUR` (6) por hora, e nunca a mesma ação com
  os mesmos parâmetros duas vezes em 30 minutos.
- Ela só age quando pode falar (para te contar) e nunca pausada (`/pausar`).
- Se a ação falha ou é negada, ela não diz que fez.
- Tudo fica na memória (`iniciativa`) e no log. `INITIATIVE_ENABLED=false`
  desliga.
