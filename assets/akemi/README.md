# Akemi — visual oficial (skin do avatar)

Estilo anime, desenhado **só com curvas** (caminhos de Bézier): nenhum círculo,
retângulo ou polígono. Cada quadro é um SVG e um PNG transparente.

| Cor | Hex | Onde |
|---|---|---|
| Rosa vibrante | `#FC3B7F` | cor principal: moletom, pontas do cabelo, listra das meias |
| Roxo profundo | `#5420A8` | contraste: cabelo, saia, sapatos, topo da íris |
| Azul-ciano | `#42D9E8` | detalhes e acessórios: olhos, presilhas, cordões, pingente, cadarços |
| Lilás pastel | `#E9D5FF` | suavidade: forro do capuz, punhos, meias, brilho do cabelo |
| Creme | `#FFF1E6` | partes neutras: pele |

Sombras e contorno são tons puxados dessas cores (veja o topo de
`scripts/desenhar_akemi.py`).

![Os cinco quadros](folha.png)

Quadros: `parado`, `piscando`, `falando`, `feliz`, `dormindo` (os nomes que o
avatar procura). Para usar, no `.env`:

```
AVATAR_SKIN=assets/akemi
```

Para mexer no desenho, edite `scripts/desenhar_akemi.py` e gere de novo:

```
python scripts/desenhar_akemi.py
node scripts/renderizar_akemi.js 2   # PNG em 2x (precisa do playwright)
```
