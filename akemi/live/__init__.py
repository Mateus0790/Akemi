"""Chat da live: agrupamento, moderação e seleção das mensagens.

Não conecta em nenhuma plataforma. Quem quiser ligar uma (YouTube, Twitch,
Discord...) só precisa chamar ``ChatBatcher.add(ChatMessage(...))``.
"""

from .chat import ChatBatcher, ChatMessage

__all__ = ["ChatBatcher", "ChatMessage"]
