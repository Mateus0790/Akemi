# Desempenho: por que ela demora e o que pesa no PC

O caminho da sua voz até a resposta:

```
você para de falar
  └─ silêncio que encerra a frase (MIC_END_SILENCE_MS, 700 ms)
  └─ fila do Whisper (se ele estava ocupado com o áudio do PC)
  └─ Whisper transcreve (CPU ou placa de vídeo)
  └─ reconhecimento da sua voz (se cadastrada)
  └─ cérebro: memória + modelo de linguagem até a 1ª frase
  └─ voz: Fish Audio até o primeiro som
ela começa a falar
```

A parte que mais pesa na CPU é o **Whisper**. Ele roda a cada frase que o
microfone ouve (no modo `wake_word` ele precisa transcrever para achar o nome
dela) e, com `SYSTEM_AUDIO_ENABLED=true`, a cada trecho com fala no áudio do PC
(vídeo, jogo, Discord). O resto (captura, VAD, eventos) custa microssegundos.

## O cronômetro

A cada resposta à sua voz, o terminal mostra uma linha assim:

```
voice reply latency  total_ms=3150 end_silence_ms=700 stt_queue_ms=0 stt_ms=1400 voice_id_ms=60 brain_ms=990
fish first audio     ms=620
```

| Campo | O que é | Se estiver alto |
|---|---|---|
| `end_silence_ms` | Quanto silêncio ela espera para saber que você terminou. | `MIC_END_SILENCE_MS=500` (menos que isso pode cortar você no meio da frase). |
| `stt_queue_ms` | Esperando o Whisper terminar um trecho do áudio do PC. | Baixe `SYSTEM_AUDIO_MAX_LOAD` ou desligue `SYSTEM_AUDIO_ENABLED`. |
| `stt_ms` | O Whisper transcrevendo a sua frase. | Placa NVIDIA: `WHISPER_DEVICE=cuda`. Sem placa: `WHISPER_MODEL=base` (mais rápido, erra mais). |
| `voice_id_ms` | Conferir se a voz é sua. | Costuma ser pouco; se não for, `VOICE_ID_ENABLED=false` para testar. |
| `brain_ms` | Memória + modelo de linguagem até a primeira frase pronta. | É a mesma demora de quando você digita. Troque o `FAST_MODEL` ou o provedor. |
| `total_ms` | Do fim da sua fala até ela começar a responder (sem o `fish first audio`). | |

`fish first audio` é quanto a nuvem do Fish Audio levou até o primeiro som
dessa frase (`FISH_LATENCY=balanced` ou `low` começam antes).

## Quanto o Whisper ocupa

A cada minuto em que ele trabalhou, aparece:

```
whisper load  busy=64% mic=12 trechos, 9.8s pc=21 trechos, 28.6s pc_skipped=7 window_s=60
```

`busy` é a fração do minuto em que o Whisper estava rodando. Perto de 100%,
ele está ocupando a CPU o tempo todo; é isso que deixa o PC pesado. `mic`
mostra quanto foi a sua voz (e tudo o que o microfone ouviu), `pc` o áudio do
PC, e `pc_skipped` os trechos do PC descartados para não atrapalhar você.

Se `mic` estiver alto sem você estar falando, o microfone está ouvindo outra
coisa (caixa de som, TV): use fone, ou suba `VAD_AGGRESSIVENESS=3`.

## O que a Akemi já faz para não pesar

- **A sua voz vai na frente.** O áudio do PC nunca entra na fila na frente
  dela, e enquanto você está falando ele nem começa a transcrever. Só um
  trecho do PC que já estava rodando faz você esperar (`stt_queue_ms`).
- **Limite de CPU para o áudio do PC.** `SYSTEM_AUDIO_MAX_LOAD=0.25`: o
  Whisper passa no máximo cerca de 25% do tempo com o áudio do PC. Num PC
  rápido nada se perde; num lento, os trechos que passarem do limite são
  descartados em vez de travar tudo.

## Ajustes

| No `.env` | Padrão | Efeito |
|---|---|---|
| `WHISPER_DEVICE` | `auto` | `cuda` usa a placa NVIDIA: muito mais rápido e tira o peso da CPU. Precisa das bibliotecas CUDA 12 (cuBLAS e cuDNN 9) que o faster-whisper pede. |
| `WHISPER_MODEL` | `small` | `base` é ~2× mais rápido e erra mais em português. |
| `WHISPER_THREADS` | `0` (= 4) | Menos threads: o PC fica mais leve, o Whisper mais lento. |
| `MIC_END_SILENCE_MS` | `700` | Menos: ela responde antes, mas pode cortar você numa pausa. |
| `SYSTEM_AUDIO_MAX_LOAD` | `0.25` | Fração do tempo do Whisper para o áudio do PC (`1.0` = sem limite). |

Outras coisas que pesam, se estiverem ligadas: a leitura da tela (OCR do
Tesseract a cada `SCREEN_INTERVAL_S`) e as vozes locais (Kokoro e Piper rodam
na CPU; o Fish Audio roda na nuvem).

O núcleo de áudio em Rust ([RUST.md](RUST.md)) deixa a captura e o VAD mais
rápidos, mas eles já custavam menos de 0,3% da CPU: não é ele que resolve isso.
