"""Avatar de desktop: a Akemi sentada nas suas janelas.

Ideia do Capri (github.com/lachydotmcg/capri-desktop), um "desktop pet" que senta
nas janelas, pisca, mexe a boca quando fala e dorme quando você some. Aqui foi
reescrito do zero e com um visual próprio da Akemi (nada do código nem da arte
do Capri foi copiado).

- ``model.py``: o que ela está fazendo e qual cara fazer (lógica pura, testável).
- ``perch.py``: onde sentar (topo da janela em foco, ou o canto da tela).
- ``draw.py``: desenha a Akemi (ou uma skin sua, em PNG) num canvas do Tk.
- ``window.py``: a janela transparente, sempre por cima, numa thread própria.
- ``bridge.py``: liga o barramento de eventos ao avatar.

Usa só o ``tkinter`` que vem com o Python. A transparência e o "sentar na
janela" são do Windows; em outros sistemas ela fica num quadrinho no canto.
"""
