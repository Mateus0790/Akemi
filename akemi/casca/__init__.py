"""Modo Casca: a Akemi vira o corpo do Claude Code e do Codex enquanto eles trabalham.

Ideia do "Code/Husk Mode" do Capri (github.com/lachydotmcg/capri-desktop),
reescrita do zero para a Akemi (nada do código do Capri foi copiado).

Como funciona::

    Claude Code / Codex ── hook ──► akemi.casca.hook ──HTTP (127.0.0.1 + token)──►
        CascaListener ──► CascaWatch ──► legenda no avatar, fala, contexto da mente

- ``hook.py``: o comando que o Claude Code e o Codex rodam a cada evento. Só
  biblioteca padrão, para abrir rápido e nunca travar o assistente.
- ``listener.py``: recebe os eventos só de 127.0.0.1 e só com o token.
- ``watch.py``: transforma eventos em reações ("O Claude precisa do seu OK").
- ``install.py``: liga e desliga os hooks sem mexer nos seus outros hooks.

Ela só assiste: nunca aprova nada nem responde pelo assistente. Tudo o que vem
dos hooks é ``Trust.PUBLIC`` e nunca dispara ação.
"""
