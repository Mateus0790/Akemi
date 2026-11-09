"""Chaves públicas que assinam a Akemi oficial (Ed25519, base64).

Quem assina é o CI, com a chave privada guardada no secret ``AKEMI_SIGNING_KEY``
do repositório. Para criar o par de chaves, rode uma vez no seu PC::

    akemi-assinatura gerar-chave

Ele reescreve este arquivo com a chave pública e mostra a privada para você
colar no GitHub (Settings → Secrets and variables → Actions). A chave privada
nunca vai para o repositório.

Para trocar de chave sem invalidar as versões antigas, deixe as duas aqui.
"""

PUBLIC_KEYS: tuple[str, ...] = ()
