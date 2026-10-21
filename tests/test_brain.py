import time

from akemi.brain.attention import AttentionFilter, Route, looks_complex, parse_json_object
from akemi.brain.mind import SentenceStreamer
from akemi.brain.persona import Persona
from akemi.brain.state import InnerState
from akemi.brain.triggers import TriggerRule, TriggerSet
from akemi.core.events import Event, Trust
from akemi.storage.memory import Memory


def test_sentence_streamer_hides_action_tags():
    s = SentenceStreamer()
    out = []
    for chunk in ["Claro! Vou ano", "tar isso. [[acao:lem", 'brar {"texto": "x"}]] Pronto.', " [[humor:ani"]:
        out += s.feed(chunk)
    out += s.flush()
    assert out == ["Claro!", "Vou anotar isso.", "Pronto."]


def test_state_roundtrip_and_decay(tmp_path):
    st = InnerState()
    st.set_mood("animada", 0.9)
    st.add_task("revisar PR")
    st.remember_thought("o Mateus está programando há horas")
    path = tmp_path / "state.json"
    st.save(path)
    loaded = InnerState.load(path)
    assert loaded.mood == "animada" and loaded.open_tasks[0].description == "revisar PR"
    loaded.decay(now=loaded.last_updated_at + 3 * 3600)
    assert loaded.mood == "neutra"
    st.set_mood("mood-inexistente")
    assert st.mood == "animada"


def test_state_load_corrupted(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{nao é json", encoding="utf-8")
    assert InnerState.load(p).mood == "neutra"


def test_attention_routes():
    rule = TriggerRule("erro", "ambient.screen", ["error"], cooldown_s=60)
    att = AttentionFilter(TriggerSet([rule]), min_unprompted_gap_s=30)
    st = InnerState()
    now = time.time()
    owner = Event("owner.message", {"text": "oi"}, trust=Trust.OWNER)
    assert att.decide(owner, st, paused=True).route == Route.QUICK  # dono fura a pausa
    deep = Event("owner.message", {"text": "explica como funciona o event loop"}, trust=Trust.OWNER)
    assert att.decide(deep, st, paused=False).route == Route.DEEP
    fake_owner = Event("owner.message", {"text": "oi"}, trust=Trust.PUBLIC)
    assert att.decide(fake_owner, st, paused=False).route == Route.IGNORE
    chat = Event("chat.batch", trust=Trust.PUBLIC)
    assert att.decide(chat, st, paused=True).route == Route.IGNORE
    screen = Event("ambient.screen", {"text": "Fatal error"}, trust=Trust.PUBLIC)
    assert att.decide(screen, st, paused=False, now=now).route == Route.CONSIDER
    st.last_spoke_at = now
    assert att.decide(screen, st, paused=False, now=now).route == Route.OBSERVE


def test_looks_complex():
    assert not looks_complex("bom dia")
    assert looks_complex("por que meu código dá erro?")


def test_parse_json_object_tolerant():
    assert parse_json_object('claro! {"decision": "comment"} pronto') == {"decision": "comment"}
    assert parse_json_object("nada") == {}


def test_triggers_yaml_loads_and_cooldown():
    ts = TriggerSet.load()
    rule = ts.match("ambient.screen", "Traceback (most recent call last)", now=1000)
    assert rule is not None and rule.id == "erro_na_tela"
    TriggerSet.fire(rule, now=1000)
    # Em cooldown, o erro cai na regra genérica da tela, que tem o próprio cooldown.
    generic = ts.match("ambient.screen", "Traceback", now=1010)
    assert generic is not None and generic.id == "mudou_a_tela"
    TriggerSet.fire(generic, now=1010)
    assert ts.match("ambient.screen", "Traceback", now=1020) is None


def test_persona_loads_with_owner():
    p = Persona.load(owner="Fulano")
    prompt = p.system_prompt(live=True)
    assert "Fulano" in prompt and "Regras da live" in prompt
    assert "Regras da live" not in p.system_prompt(live=False)


def test_memory_facts_and_recall():
    m = Memory()
    assert m.add_fact("O Mateus estuda ADS")
    assert not m.add_fact("O Mateus estuda ADS")
    m.add_episode("conversa", "Conversamos sobre o servidor ZimaOS e Docker", ts=1)
    m.add_episode("conversa", "Falamos de anime", ts=2)
    hits = m.recall("como está o docker do servidor?")
    assert hits and "ZimaOS" in hits[0].text
    assert m.forget_fact("ADS") == 1 and m.facts() == []
    m.close()


# ------------------------------------------------- pensamentos repetidos
# Pensamentos reais salvos no state.json do dono (resumidos).
REPEATED = [
    "Estou acompanhando o trabalho do Mateus na branch fix/tessdata-windows. Parece que ele está testando "
    "algo relacionado à leitura de imagens, já que várias janelas de BongoCat e Claude aparecem.",
    "Observando o Mateus testar a leitura de imagens na branch fix/tessdata-windows. Várias janelas de "
    "BongoCat e Claude estão abertas, mostrando resultados de análise OCR.",
    "Estou observando o Mateus trabalhar na branch fix/tessdata-windows. Ele parece estar testando a "
    "leitura de imagens de gráficos, com várias janelas de análise aberta.",
]


def test_repeated_thoughts_are_not_stored():
    from akemi.brain.state import InnerState

    s = InnerState()
    assert s.remember_thought(REPEATED[0])
    assert not s.remember_thought(REPEATED[1])
    assert not s.remember_thought(REPEATED[2])
    assert s.remember_thought("Queria que ele me deixasse escolher a música da live hoje.")
    assert len(s.recent_thoughts) == 2


def test_old_state_file_is_cleaned_on_load():
    from akemi.brain.state import InnerState

    s = InnerState.from_dict({"mood": "curiosa", "recent_thoughts": REPEATED + [REPEATED[0]]})
    assert len(s.recent_thoughts) == 1 and s.mood == "curiosa"


def test_long_thought_is_trimmed():
    from akemi.brain.state import MAX_THOUGHT_CHARS, InnerState

    s = InnerState()
    s.remember_thought("palavra " * 200)
    assert len(s.recent_thoughts[0]) <= MAX_THOUGHT_CHARS
