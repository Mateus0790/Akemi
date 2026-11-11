"""Gera a arte da Akemi (estilo anime) em SVG, só com curvas.

Nada de círculo, retângulo ou polígono: cada parte é um caminho de curvas de
Bézier desenhado à mão. Sai um SVG por quadro da skin do avatar (``parado``,
``piscando``, ``falando``, ``feliz``, ``dormindo``) e uma folha com a paleta.

    python scripts/desenhar_akemi.py            # escreve assets/akemi/*.svg
    node scripts/renderizar_akemi.js            # (opcional) vira PNG

Depois aponte ``AVATAR_SKIN=assets/akemi`` no ``.env``.
"""

from __future__ import annotations

import re
from pathlib import Path

# ------------------------------------------------------------------ paleta
ROSA = "#FC3B7F"  # cor principal (moletom)
ROXO = "#5420A8"  # contraste (cabelo, saia, sapatos)
CIANO = "#42D9E8"  # detalhes e acessórios (olhos, cordões, presilhas)
LILAS = "#E9D5FF"  # suavidade (forro, meias, brilhos)
CREME = "#FFF1E6"  # partes neutras (pele)

# Tons derivados (sombra e linha) — sempre puxados das cores da paleta.
LINHA = "#2A0E5A"
ROSA_SOMBRA = "#D9286A"
ROXO_SOMBRA = "#3B1380"
CREME_SOMBRA = "#F5D2C4"
BOCA = "#9E1F55"
LINGUA = "#FF8DB3"

W, H = 400, 560
OUT = Path(__file__).resolve().parent.parent / "assets" / "akemi"

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def mirror(d: str, axis: float = 200.0) -> str:
    """Espelha um caminho (só comandos absolutos M/C/S/Q/L) no eixo x = axis."""
    out, is_x = [], True
    pos = 0
    for m in _NUM.finditer(d):
        out.append(d[pos:m.start()])
        v = float(m.group())
        out.append(f"{(2 * axis - v) if is_x else v:g}")
        is_x = not is_x
        pos = m.end()
    out.append(d[pos:])
    return "".join(out)


def path(d: str, fill: str = "none", stroke: str = LINHA, sw: float = 3, extra: str = "") -> str:
    s = f'stroke="{stroke}" stroke-width="{sw}"' if stroke != "none" else 'stroke="none"'
    return f'<path d="{d}" fill="{fill}" {s} stroke-linecap="round" stroke-linejoin="round" {extra}/>'


# ------------------------------------------------------------------ peças
DEFS = f"""
<defs>
  <linearGradient id="cabelo" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{ROXO}"/>
    <stop offset="0.62" stop-color="{ROXO}"/>
    <stop offset="1" stop-color="{ROSA}"/>
  </linearGradient>
  <linearGradient id="mecha" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{ROXO}"/>
    <stop offset="0.55" stop-color="{ROXO}"/>
    <stop offset="1" stop-color="{ROSA}"/>
  </linearGradient>
  <linearGradient id="iris" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{ROXO}"/>
    <stop offset="0.45" stop-color="{CIANO}"/>
    <stop offset="1" stop-color="{LILAS}"/>
  </linearGradient>
  <linearGradient id="moletom" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{ROSA}"/>
    <stop offset="1" stop-color="{ROSA_SOMBRA}"/>
  </linearGradient>
  <filter id="esfuma" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur stdDeviation="4"/>
  </filter>
  <clipPath id="rosto">
    <path d="M138 146 C136 196 156 236 186 252 C194 257 206 257 214 252 C244 236 264 196 262 146 C258 96 142 96 138 146 Z"/>
  </clipPath>
</defs>"""

CABELO_TRAS = (
    "M200 40 C130 38 84 92 86 166 C88 226 76 282 52 334 C44 352 36 366 26 376 "
    "C56 378 80 366 96 348 C94 368 88 384 78 400 C108 394 130 376 142 352 "
    "C148 334 152 312 156 290 C180 300 220 300 244 290 C248 314 254 336 262 354 "
    "C274 378 296 394 326 398 C314 384 308 368 306 350 C322 366 346 376 374 372 "
    "C362 360 352 344 346 326 C326 276 314 226 314 166 C316 92 270 38 200 40 Z"
)
ROSTO = "M138 146 C136 196 156 236 186 252 C194 257 206 257 214 252 C244 236 264 196 262 146 C258 96 142 96 138 146 Z"
PESCOCO = "M188 236 C188 252 187 266 184 282 C194 289 206 289 216 282 C213 266 212 252 212 236 Z"
SOMBRA_QUEIXO = "M187 254 C194 262 206 262 213 254 C213 262 214 270 215 276 C205 281 195 281 185 276 C186 268 187 260 187 254 Z"
MOLETOM = (
    "M176 268 C148 270 118 282 106 302 C94 322 90 354 92 386 C94 406 104 420 120 424 "
    "C160 432 240 432 280 424 C296 420 306 406 308 386 C310 354 306 322 294 302 "
    "C282 282 252 270 224 268 C214 276 186 276 176 268 Z"
)
GOLA = "M146 276 C158 262 178 264 200 272 C222 264 242 262 254 276 C246 298 226 308 200 308 C174 308 154 298 146 276 Z"
GOLA_DENTRO = "M166 274 C178 270 190 272 200 279 C210 272 222 270 234 274 C228 290 214 297 200 297 C186 297 172 290 166 274 Z"
BOLSO = "M140 362 C172 354 228 354 260 362 C266 382 268 400 264 414 C222 420 178 420 136 414 C132 400 134 382 140 362 Z"
MANGA_E = (
    "M120 294 C100 308 90 340 94 370 C98 390 112 400 132 402 C150 404 166 400 176 394 "
    "C170 384 160 378 146 376 C136 352 134 326 132 304 Z"
)
PUNHO_E = "M150 374 C162 372 174 378 180 388 C178 398 172 404 162 406 C154 400 148 388 150 374 Z"
MAO_E = "M174 384 C184 378 198 382 200 394 C200 404 188 410 178 406 C170 402 168 390 174 384 Z"
SAIA = (
    "M118 418 C150 432 250 432 282 418 C292 434 300 448 306 460 "
    "C252 474 148 474 94 460 C100 448 108 434 118 418 Z"
)
PREGAS = [
    "M150 430 C146 442 142 454 138 468",
    "M184 432 C182 446 180 458 180 472",
    "M216 432 C218 446 220 458 220 472",
    "M250 430 C254 442 258 454 262 468",
]
PERNA_E = "M150 456 C148 482 150 508 154 528 C162 532 172 532 178 528 C180 506 182 480 182 460 Z"
MEIA_E = "M149 484 C158 488 174 488 182 484 C181 500 180 514 178 528 C170 532 162 532 154 528 C151 514 149 500 149 484 Z"
LISTRA_E = "M149 484 C158 488 174 488 182 484 C182 489 182 492 182 494 C174 498 158 498 149 494 C149 491 149 488 149 484 Z"
SAPATO_E = "M151 520 C146 530 138 540 140 548 C148 557 182 557 188 548 C189 538 184 528 180 520 C170 527 160 527 151 520 Z"
CADARCO_E = "M156 532 C162 528 170 528 176 532"

CABELO_FRENTE = (
    "M200 44 C140 44 112 86 108 140 C106 170 112 200 122 228 "
    "C126 210 130 190 136 176 C140 162 144 150 148 140 "
    "C150 152 154 160 160 166 C162 150 166 138 172 128 "
    "C176 140 182 148 190 154 C192 138 196 128 202 118 "
    "C208 130 216 142 228 154 C228 144 230 136 234 126 "
    "C240 138 244 152 244 164 C250 154 256 146 262 140 "
    "C266 164 270 192 278 228 C290 198 294 168 292 140 C288 86 260 44 200 44 Z"
)
SOMBRA_FRANJA = (
    "M136 176 C140 162 144 150 148 140 C150 152 154 160 160 166 C162 150 166 138 172 128 "
    "C176 140 182 148 190 154 C192 138 196 128 202 118 C208 130 216 142 228 154 "
    "C228 146 230 136 234 126 C240 138 244 152 244 164 C250 154 256 146 262 140 "
    "C264 150 266 160 268 172 C262 168 256 170 250 184 C246 176 240 170 232 176 "
    "C226 170 214 166 200 168 C186 166 176 172 168 182 C164 178 156 180 150 190 Z"
)
MECHA_E = "M140 128 C126 170 122 222 130 264 C134 286 132 302 124 320 C142 310 154 290 156 266 C158 232 152 190 152 150 Z"
FIO_TOPO = "M198 48 C188 28 198 14 220 10 C208 20 204 32 208 48 Z"
BRILHO_E = "M132 104 C150 84 172 74 194 74 C178 82 160 92 146 108 C142 112 136 110 132 104 Z"
BRILHO_D = "M246 80 C262 86 276 98 284 112 C276 108 266 104 254 100 C250 94 248 88 246 80 Z"
FIOS = [
    "M168 64 C150 80 136 104 130 132",
    "M232 62 C252 76 268 102 272 130",
    "M200 56 C196 74 196 92 200 110",
]


def olho(cx: float, kind: str, lado: int) -> str:
    """Um olho de anime. ``lado`` = -1 (esquerdo) ou 1 (direito)."""
    # Desenhado para o olho esquerdo com centro em (170, 180); o direito é espelhado.
    dx = cx - 170

    def mv(d: str) -> str:
        d = re.sub(r"(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)", lambda m: f"{float(m.group(1)) + dx:g} {m.group(2)}", d)
        return mirror(d, cx) if lado == 1 else d

    if kind == "fechado":
        return path(mv("M148 186 C156 194 176 196 192 186"), sw=4) + path(mv("M148 186 C144 184 142 180 142 176"), sw=3)
    if kind == "feliz":
        return path(mv("M150 190 C156 172 182 170 190 190"), sw=4.5)
    if kind == "dormindo":
        return (path(mv("M150 188 C158 196 178 197 190 188"), sw=3.5)
                + path(mv("M156 196 C158 200 160 202 162 204"), sw=2)
                + path(mv("M168 199 C168 203 168 205 168 207"), sw=2))
    branco = mv("M150 176 C152 160 166 154 180 158 C190 162 194 176 192 190 C188 202 176 207 164 203 C154 198 148 188 150 176 Z")
    iris = mv("M156 179 C156 165 166 159 176 162 C186 166 189 180 187 192 C185 202 175 206 167 202 C159 197 156 189 156 179 Z")
    pupila = mv("M166 182 C166 173 176 172 178 181 C179 191 168 193 166 182 Z")
    cilio = mv("M143 179 C145 161 162 151 180 152 C189 153 195 158 198 166 C191 160 182 156 171 157 "
               "C160 158 152 166 148 181 Z")
    ponta = mv("M146 168 C141 166 138 161 138 155 C142 160 146 162 150 162 Z")
    baixo = mv("M160 205 C168 208 177 207 186 201")
    # brilhos sempre do mesmo lado (a luz vem da esquerda), como no anime
    bx = cx - 9 if lado == -1 else cx - 7
    brilho = (f"M{bx - 1:g} 171 C{bx - 1:g} 164 {bx + 7:g} 164 {bx + 8:g} 170 "
              f"C{bx + 9:g} 177 {bx:g} 179 {bx - 1:g} 171 Z")
    brilho2 = (f"M{bx + 12:g} 193 C{bx + 12:g} 190 {bx + 16:g} 189 {bx + 17:g} 192 "
               f"C{bx + 17:g} 195 {bx + 13:g} 196 {bx + 12:g} 193 Z")
    return "".join([
        path(branco, fill="#FFFDFB", sw=0, stroke="none"),
        path(iris, fill="url(#iris)", sw=1.5, stroke=ROXO_SOMBRA),
        path(pupila, fill=LINHA, stroke="none"),
        path(brilho, fill="#FFFFFF", stroke="none"),
        path(brilho2, fill="#FFFFFF", stroke="none", extra='opacity="0.9"'),
        path(cilio, fill=LINHA, sw=1.5),
        path(ponta, fill=LINHA, sw=1.2),
        path(baixo, sw=2),
    ])


def boca(kind: str) -> str:
    if kind == "aberta":
        return (path("M188 228 C196 232 204 232 212 228 C211 240 205 247 200 247 C195 247 189 240 188 228 Z", fill=BOCA, sw=2.5)
                + path("M193 241 C196 237 204 237 207 241 C204 246 196 246 193 241 Z", fill=LINGUA, stroke="none"))
    if kind == "sorriso":
        return (path("M182 225 C194 233 206 233 218 225 C216 241 207 250 200 250 C193 250 184 241 182 225 Z", fill=BOCA, sw=2.5)
                + path("M190 243 C195 238 205 238 210 243 C206 249 194 249 190 243 Z", fill=LINGUA, stroke="none")
                + path("M184 227 C194 232 206 232 216 227 C214 231 186 231 184 227 Z", fill="#FFFDFB", stroke="none"))
    if kind == "dormindo":
        return path("M195 232 C198 235 202 235 205 232", sw=2.2)
    return path("M189 229 C195 235 205 235 211 229", sw=2.5)  # sorrisinho


def bochechas(forte: bool) -> str:
    op = "0.75" if forte else "0.5"
    out = []
    for d in ("M142 212 C148 202 170 202 176 212 C170 222 148 222 142 212 Z",):
        for dd in (d, mirror(d)):
            out.append(path(dd, fill=ROSA, stroke="none", extra=f'opacity="{op}" filter="url(#esfuma)"'))
    for d in ("M150 214 C152 210 154 208 156 206", "M158 215 C160 211 162 209 164 207", "M166 215 C168 211 170 209 172 207"):
        for dd in (d, mirror(d)):
            out.append(path(dd, stroke=ROSA_SOMBRA, sw=1.6, extra='opacity="0.8"'))
    return "".join(out)


def presilhas() -> str:
    """Duas presilhas ciano cruzadas na franja e um pingente na mecha."""
    return "".join([
        path("M244 96 C254 92 264 92 274 96", stroke=LINHA, sw=8),
        path("M244 96 C254 92 264 92 274 96", stroke=CIANO, sw=5),
        path("M248 108 C256 100 266 96 278 94", stroke=LINHA, sw=8),
        path("M248 108 C256 100 266 96 278 94", stroke=CIANO, sw=5),
        path("M250 98 C254 97 258 96 262 96", stroke="#FFFFFF", sw=1.6, extra='opacity="0.8"'),
        # pingente em gota na ponta da mecha esquerda
        path("M128 318 C122 328 122 338 129 342 C136 338 136 328 128 318 Z", fill=CIANO, sw=2),
        path("M127 328 C126 332 127 335 129 336", stroke="#FFFFFF", sw=1.4),
    ])


def corpo() -> str:
    partes = []
    # pernas balançando (a direita espelhada e um pouco girada)
    perna = "".join([
        path(PERNA_E, fill=CREME, sw=2.5),
        path(MEIA_E, fill=LILAS, sw=2.5),
        path(LISTRA_E, fill=ROSA, sw=2),
        path(SAPATO_E, fill=ROXO, sw=2.5),
        path(CADARCO_E, stroke=CIANO, sw=3),
        path("M146 546 C156 550 174 550 184 546", stroke=ROXO_SOMBRA, sw=2),
    ])
    perna_d = "".join(
        path(mirror(d), fill=f, sw=sw) for d, f, sw in (
            (PERNA_E, CREME, 2.5), (MEIA_E, LILAS, 2.5), (LISTRA_E, ROSA, 2), (SAPATO_E, ROXO, 2.5))
    ) + path(mirror(CADARCO_E), stroke=CIANO, sw=3) + path(mirror("M146 546 C156 550 174 550 184 546"), stroke=ROXO_SOMBRA, sw=2)
    partes.append(f'<g transform="rotate(5 166 458)">{perna}</g>')
    partes.append(f'<g transform="rotate(-8 234 458)">{perna_d}</g>')
    # saia
    partes.append(path(SAIA, fill=ROXO, sw=3))
    partes += [path(d, stroke=ROXO_SOMBRA, sw=2.2) for d in PREGAS]
    # moletom
    partes.append(path(MOLETOM, fill="url(#moletom)", sw=3))
    partes.append(path("M112 404 C150 414 250 414 288 404", stroke=ROSA_SOMBRA, sw=2.5))
    partes.append(path(BOLSO, fill="none", stroke=ROSA_SOMBRA, sw=2.5))
    partes.append(path("M138 364 C146 380 148 396 146 412", stroke=ROSA_SOMBRA, sw=2))
    partes.append(path(mirror("M138 364 C146 380 148 396 146 412"), stroke=ROSA_SOMBRA, sw=2))
    # dobras do tecido
    partes.append(path("M124 322 C130 334 132 346 132 360", stroke=ROSA_SOMBRA, sw=2))
    partes.append(path(mirror("M124 322 C130 334 132 346 132 360"), stroke=ROSA_SOMBRA, sw=2))
    # mangas, punhos e mãos no colo
    for espelho in (False, True):
        f = mirror if espelho else (lambda d: d)
        partes.append(path(f(MANGA_E), fill="url(#moletom)", sw=3))
        partes.append(path(f("M118 330 C116 346 118 360 124 372"), stroke=ROSA_SOMBRA, sw=2))
        partes.append(path(f(MAO_E), fill=CREME, sw=2.5))
        partes.append(path(f(PUNHO_E), fill=LILAS, sw=2.5))
    # gola do capuz, pescoço e cordões
    partes.append(path(GOLA, fill=ROSA, sw=3))
    partes.append(path(GOLA_DENTRO, fill=LILAS, sw=2.5))
    partes.append(path(PESCOCO, fill=CREME, sw=2.5))
    partes.append(path(SOMBRA_QUEIXO, fill=CREME_SOMBRA, stroke="none"))
    for d in ("M188 300 C185 318 190 334 186 352", "M212 300 C215 318 210 334 214 352"):
        partes.append(path(d, stroke=LINHA, sw=5))
        partes.append(path(d, stroke=CIANO, sw=3))
    for x in (186, 214):
        partes.append(path(f"M{x} 350 C{x - 1} 356 {x} 360 {x + 1} 364", stroke=LINHA, sw=7))
        partes.append(path(f"M{x} 350 C{x - 1} 356 {x} 360 {x + 1} 364", stroke=LILAS, sw=4))
    return "".join(partes)


def cabeca(olhos: str, boca_kind: str, corar: bool) -> str:
    partes = [
        path(ROSTO, fill=CREME, sw=3),
        # sombra da franja na testa
        '<g clip-path="url(#rosto)"><g transform="translate(0 8)">'
        + path(SOMBRA_FRANJA, fill=CREME_SOMBRA, stroke="none") + "</g></g>",
        bochechas(corar),
        olho(170, olhos, -1),
        olho(230, olhos, 1),
        # sobrancelhas finas
        path("M152 146 C160 141 172 140 182 143", sw=2.4),
        path(mirror("M152 146 C160 141 172 140 182 143"), sw=2.4),
        # nariz
        path("M199 210 C201 213 202 216 199 218", sw=2, stroke=ROSA_SOMBRA),
        boca(boca_kind),
        # franja e mechas da frente
        path(MECHA_E, fill="url(#mecha)", sw=3),
        path(mirror(MECHA_E), fill="url(#mecha)", sw=3),
        path("M140 160 C136 200 136 240 140 280", stroke=ROXO_SOMBRA, sw=2),
        path(mirror("M140 160 C136 200 136 240 140 280"), stroke=ROXO_SOMBRA, sw=2),
        path(FIO_TOPO, fill=ROXO, sw=3),
        path(CABELO_FRENTE, fill=ROXO, sw=3),
        path(BRILHO_E, fill=LILAS, stroke="none", extra='opacity="0.55"'),
        path(BRILHO_D, fill=LILAS, stroke="none", extra='opacity="0.45"'),
        *(path(d, stroke=ROXO_SOMBRA, sw=2) for d in FIOS),
        # mechas rosa no meio da franja
        path("M190 154 C192 138 196 128 202 118 C200 130 197 142 190 154 Z", fill=ROSA, sw=1.5),
        path("M244 164 C244 152 240 140 234 128 C242 138 246 152 244 164 Z", fill=ROSA, sw=1.5),
        presilhas(),
    ]
    return "".join(partes)


QUADROS = {
    # nome: (olhos, boca, bochecha forte)
    "parado": ("aberto", "sorrisinho", False),
    "piscando": ("fechado", "sorrisinho", False),
    "falando": ("aberto", "aberta", False),
    "feliz": ("feliz", "sorriso", True),
    "dormindo": ("dormindo", "dormindo", False),
}


def quadro(nome: str) -> str:
    olhos, b, corar = QUADROS[nome]
    corpo_svg = path(CABELO_TRAS, fill="url(#cabelo)", sw=3) + corpo()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
            f"<title>Akemi — {nome}</title>{DEFS}{corpo_svg}{cabeca(olhos, b, corar)}</svg>\n")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for nome in QUADROS:
        (OUT / f"{nome}.svg").write_text(quadro(nome), encoding="utf-8")
        print("ok", OUT / f"{nome}.svg")


if __name__ == "__main__":
    main()
