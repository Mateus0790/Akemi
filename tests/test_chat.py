from akemi.core.events import EventBus, Trust
from akemi.live.chat import ChatBatcher, ChatMessage
from akemi.live.twitch import parse_irc, to_chat_message
from akemi.safety.moderation import Moderator


def make():
    return ChatBatcher(EventBus(), Moderator(), max_selected=2, max_batches_per_minute=2)


def test_batcher_prefers_mentions_and_questions():
    b = make()
    b.add(ChatMessage("a", "kkkkkk"))
    b.add(ChatMessage("b", "akemi, qual seu jogo favorito?"))
    b.add(ChatMessage("c", "alguém sabe que horas começa?"))
    b.add(ChatMessage("d", "!comando"))
    ev = b.flush(now=1000)
    users = [m["user"] for m in ev.payload["messages"]]
    assert users == ["b", "c"]
    assert ev.trust == Trust.PUBLIC


def test_batcher_blocks_offensive_and_dedupes_users():
    b = make()
    assert not b.add(ChatMessage("troll", "vai se matar"))
    b.add(ChatMessage("x", "akemi oi"))
    b.add(ChatMessage("x", "akemi oi de novo?"))
    ev = b.flush(now=1000)
    assert len(ev.payload["messages"]) == 1
    assert b.blocked == 1


def test_batcher_rate_limit_and_user_cooldown():
    b = make()
    for i, now in enumerate((1000, 1001, 1002)):
        b.add(ChatMessage(f"u{i}", "akemi tudo bem?"))
        ev = b.flush(now=now)
        assert (ev is not None) == (i < 2)  # 2 lotes por minuto
    b2 = make()
    b2.add(ChatMessage("x", "akemi pergunta?"))
    b2.flush(now=1000)
    b2.add(ChatMessage("x", "akemi outra pergunta?"))
    assert b2.flush(now=1005) is None  # cooldown por usuário


def test_parse_twitch_privmsg():
    raw = (
        "@badges=subscriber/1;display-name=Fulano;first-msg=1;mod=0;subscriber=1 "
        ":fulano!fulano@fulano.tmi.twitch.tv PRIVMSG #canal :oi akemi; tudo bem?\r\n"
    )
    msg = to_chat_message(parse_irc(raw))
    assert msg.user == "Fulano" and msg.text == "oi akemi; tudo bem?"
    assert msg.first_msg and msg.is_sub and not msg.is_mod


def test_parse_ping():
    irc = parse_irc("PING :tmi.twitch.tv")
    assert irc.command == "PING" and irc.params == ["tmi.twitch.tv"]
