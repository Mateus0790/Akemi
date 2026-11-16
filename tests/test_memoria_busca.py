"""Busca por significado (embeddings) e fatos escolhidos pelo assunto."""

import sqlite3
import time

import httpx
import numpy as np
import pytest

from akemi.storage.embeddings import Embedder, OpenAICompatibleEmbedder, normalize
from akemi.storage.memory import Memory

# Embedder de mentira: cada "conceito" é uma dimensão; sinônimos caem na mesma.
CONCEPTS = {
    "cachorro": 0, "cão": 0, "thor": 0, "veterinário": 0, "pet": 0,
    "servidor": 1, "zimaos": 1, "docker": 1,
    "prova": 2, "faculdade": 2, "cálculo": 2,
    "comida": 3, "pizza": 3, "lasanha": 3,
}


def fake_vec(text: str) -> np.ndarray:
    v = np.full(len(set(CONCEPTS.values())) + 1, 0.01, dtype=np.float32)
    for word in text.lower().replace(",", " ").replace(".", " ").split():
        if word in CONCEPTS:
            v[CONCEPTS[word]] += 1.0
    return normalize(v)


class FakeEmbedder(Embedder):
    name = "fake"
    model = "fake-1"

    def __init__(self):
        self.calls = 0

    async def embed(self, texts, purpose="passage"):
        self.calls += 1
        return [fake_vec(t) for t in texts]


def index(m: Memory, model: str = "fake-1") -> None:
    todo = m.unembedded(model, 1000)
    m.store_vectors(model, [(o, r, t, fake_vec(t)) for o, r, t in todo])


def test_semantic_finds_same_meaning_with_other_words():
    m = Memory()
    m.add_episode("conversa", "Mateus: levei o Thor no veterinário hoje", ts=time.time() - 3600)
    m.add_episode("conversa", "Mateus: instalei o Docker no servidor", ts=time.time() - 3600)
    # Só por palavra, "cachorro" não acha nada.
    assert m.recall("como está meu cachorro?") == []
    index(m)
    found = m.recall("como está meu cachorro?", query_vec=fake_vec("cachorro"), model="fake-1")
    assert found and "Thor" in found[0].text
    assert all("Docker" not in r.text for r in found)  # pouco parecido não entra


def test_keyword_still_works_with_vectors():
    m = Memory()
    m.add_episode("conversa", "falamos de kubernetes", ts=1)
    index(m)
    assert m.recall("kubernetes", query_vec=fake_vec("kubernetes"), model="fake-1")[0].text == "falamos de kubernetes"


def test_vectors_of_other_model_are_ignored_and_reindexed():
    m = Memory()
    m.add_episode("conversa", "levei o Thor no veterinário", ts=1)
    index(m, model="velho")
    assert m.recall("cachorro", query_vec=fake_vec("cachorro"), model="novo") == []
    assert m.unembedded("novo")  # trocar de modelo recalcula
    index(m, model="novo")
    assert m.recall("cachorro", query_vec=fake_vec("cachorro"), model="novo")


def test_deleting_or_summarizing_removes_vectors():
    m = Memory()
    m.add_episode("conversa", "levei o Thor no veterinário", ts=1)
    index(m)
    ep = m.old_episodes(before=10)[0]
    m.replace_with_summary([ep.id], "Resumo: dia de pet", ts=1)
    assert m.stats()["com_vetor"] == 0
    # O vetor de um episódio que sumiu enquanto era calculado não é guardado,
    # nem cai no resumo que reaproveitou o mesmo id.
    assert m.store_vectors("fake-1", [("ep", ep.id, ep.text, fake_vec(ep.text))]) == 0
    m.add_fact("o cachorro do Mateus se chama Thor")
    index(m)
    m.forget_fact("Thor")
    with m._lock:
        assert m._db.execute("SELECT COUNT(*) FROM vectors WHERE owner='fact'").fetchone()[0] == 0


def test_facts_for_prefers_the_subject_over_the_newest():
    m = Memory()
    m.add_fact("o cachorro do Mateus se chama Thor")
    for i in range(30):
        m.add_fact(f"detalhe qualquer número {i}")
    assert "o cachorro do Mateus se chama Thor" not in m.facts(15)  # o problema antigo
    by_word = m.facts_for("e o Thor, como está?", 15)
    assert by_word[0] == "o cachorro do Mateus se chama Thor"
    assert len(by_word) == 15
    index(m)
    by_meaning = m.facts_for("e o pet?", 15, query_vec=fake_vec("pet"), model="fake-1")
    assert by_meaning[0] == "o cachorro do Mateus se chama Thor"


def test_facts_for_without_subject_is_the_newest():
    m = Memory()
    for i in range(5):
        m.add_fact(f"fato {i}")
    assert m.facts_for("", 3) == m.facts(3)


def test_old_database_gets_facts_searchable(tmp_path):
    path = tmp_path / "memory.sqlite3"
    db = sqlite3.connect(path)  # banco no formato antigo, sem facts_fts
    db.executescript("CREATE TABLE facts (id INTEGER PRIMARY KEY, ts REAL NOT NULL, text TEXT NOT NULL UNIQUE);")
    db.execute("INSERT INTO facts(ts, text) VALUES (1, 'Mateus estuda cálculo na faculdade')")
    db.commit()
    db.close()
    m = Memory(path)
    assert m.facts_for("como foi a faculdade?", 5)[0] == "Mateus estuda cálculo na faculdade"
    m.close()
    assert Memory(path).facts(5) == ["Mateus estuda cálculo na faculdade"]  # reabrir não duplica


@pytest.mark.asyncio
async def test_openai_compatible_embedder_sends_input_type_for_nim():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [0.0, 2.0]}, {"index": 0, "embedding": [3.0, 4.0]},
        ]})

    client = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler))
    emb = OpenAICompatibleEmbedder("http://x", "nv", name="nim", input_type=True, client=client)
    a, b = await emb.embed(["um", "dois"], purpose="query")
    assert seen["input_type"] == "query" and seen["model"] == "nv"
    assert np.allclose(a, [0.6, 0.8]) and np.allclose(b, [0.0, 1.0])  # ordem por index, normalizado


@pytest.mark.asyncio
async def test_orchestrator_indexes_and_mind_uses_meaning(tmp_path):
    from akemi.brain.providers import EchoProvider
    from akemi.core.config import Settings
    from akemi.core.orchestrator import Akemi

    emb = FakeEmbedder()
    settings = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path)
    orch = Akemi(settings, fast=EchoProvider(), embedder=emb, persist=False, desktop_actions=False)
    orch.memory.add_episode("conversa", "Mateus: levei o Thor no veterinário", ts=time.time() - 3600)
    orch.memory.add_fact("Mateus ama lasanha")
    assert await orch.index_memory() == 2
    assert await orch.index_memory() == 0  # nada novo
    qv = await orch.mind._embed_query("como está meu cachorro?")
    block = orch.mind._context_block("como está meu cachorro?", query_vec=qv)
    assert "Thor" in block
    assert "busca por significado: fake/fake-1" in orch.describe_memory()


@pytest.mark.asyncio
async def test_mind_survives_embedder_failure(tmp_path):
    from akemi.brain.providers import EchoProvider
    from akemi.core.config import Settings
    from akemi.core.orchestrator import Akemi

    class Broken(Embedder):
        model = "x"

        async def embed(self, texts, purpose="passage"):
            raise RuntimeError("offline")

    settings = Settings(_env_file=None, brain_provider="echo", data_dir=tmp_path)
    orch = Akemi(settings, fast=EchoProvider(), embedder=Broken(), persist=False, desktop_actions=False)
    assert await orch.mind._embed_query("oi") is None
    orch.memory.add_fact("algo")
    with pytest.raises(RuntimeError):
        await orch.index_memory()  # o ticker pega e só avisa
