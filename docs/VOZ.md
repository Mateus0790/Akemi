# Reconhecer você pela voz

Sem cadastro, tudo o que chega pelo microfone com "Akemi, ..." vale como
você: um amigo do lado ou um vídeo alto perto do mic também mandam nela.
Com a sua voz cadastrada, ela confere cada frase antes:

| Quem ela acha que é | O que acontece |
|---|---|
| **você** (a voz confere) | tudo como sempre: ações, lembretes, memória |
| **outra pessoa** | conversa como com uma visita: simpática, mas **sem ações**, sem guardar fatos e sem contar nada pessoal seu |
| **incerto** (barulho, longe do mic, voz rouca) | também sem ações. Ela pede para repetir mais perto ou confirmar pelo terminal ou pelo app |

Na dúvida, ela fica do lado seguro: recusar você às vezes é chato, aceitar
outra pessoa é perigoso. O terminal e o app do celular (com o `AKEMI_TOKEN`)
continuam valendo como você, então dá para resolver por eles quando ela não
te reconhecer.

## Instalar

```powershell
pip install -e ".[voz]"
curl.exe -L -o data\voices\wespeaker_en_voxceleb_resnet34.onnx https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34.onnx
```

O modelo tem ~26 MB e roda offline, na CPU. Apesar do "en" no nome, ele
reconhece a voz, não as palavras, e funciona com português. Outro caminho:
`VOICE_MODEL_PATH` no `.env`.

## Cadastrar

```powershell
akemi --cadastrar-voz
```

Você lê 8 frases em voz alta. Faça isso **no lugar e com o microfone de
sempre**: o cadastro aprende também como o seu microfone soa. Silêncio em
volta ajuda. Se as frases não parecerem da mesma pessoa (barulho, alguém
falando junto), ela recusa e pede para tentar de novo.

Depois é usar normalmente com `akemi --mic`. No console:

- `/voz`: cadastro, limites, e quantas frases ela achou suas, de outra pessoa
  ou incertas nesta sessão (com a nota da última)
- `akemi --esquecer-voz`: apaga o cadastro (o microfone volta a valer como você)
- Para recadastrar, é só rodar `--cadastrar-voz` de novo

## Detalhes que importam

- **"Sim", "pode", "não"** são curtos demais para conferir a voz. Eles valem
  se vierem logo depois (até `FOLLOWUP_WINDOW_S`, 20 s) de uma frase que ela
  conferiu como sua. Solto, um "sim" fica como incerto.
- Uma ação que pede confirmação **só é confirmada pela sua voz**. Se outra
  pessoa disser "sim", a pergunta continua esperando você.
- O que a visita fala vai para a memória como `visita`, não como conversa
  sua: a autoevolução não aprende com outras pessoas. E ela fica sabendo
  que alguém falou, então pode te contar depois.
- Qualquer um ainda consegue **interromper** a fala dela (é só falar por
  cima). Interromper não dá poder nenhum.
- Se o cadastro existe mas o modelo não carrega (arquivo sumiu, faltou o
  `sherpa-onnx`), ela **não libera geral**: ninguém passa como você pelo
  microfone até consertar, e ela avisa (autocura, `/saude`). Para voltar ao
  comportamento antigo de propósito, use `--esquecer-voz` ou `VOICE_ID_ENABLED=false`.

## O que fica guardado

Só a **impressão da voz** (um vetor de números) e os limites, em
`data/voz.json`. Nada de áudio. Esse arquivo vai junto no chip
(`akemi-chip`), para ela continuar te reconhecendo em outro PC. Mesmo assim,
é um dado seu: não publique.

No modo servidor + corpo, o microfone e o reconhecimento ficam no PC (corpo).
Cadastre no PC e deixe o `voz.json` no `data/` de lá.

## Limites (leia antes de confiar)

- **Não é segurança de banco.** Uma gravação da sua voz, ou alguém com voz
  muito parecida, pode passar. Serve para "quem está do meu lado não manda
  nela", não contra alguém determinado.
- Ela pode **não te reconhecer** às vezes: gripe, barulho, outro microfone,
  falando de longe. Se acontecer muito, recadastre onde você mais usa.
- Nos testes com quatro vozes diferentes, ela aceitou todas as frases do
  dono e nenhuma das outras três vozes (as outras caíram como "outra pessoa"
  ou "incerto"). Mas foram vozes sintéticas, não pessoas de verdade: o
  resultado real depende do seu microfone e da sua casa.
