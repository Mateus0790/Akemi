"""Memória de longo prazo em SQLite.

- ``episodes``: o que aconteceu (conversas, coisas vistas), com busca FTS5.
- ``facts``: coisas que o dono pediu para lembrar ou que são estáveis.

Busca híbrida: palavra-chave (FTS5/bm25) + significado (embeddings, opcional,
ver :mod:`akemi.storage.embeddings`) + um empurrão para o que é recente. Os
vetores ficam na tabela ``vectors`` e são calculados aos poucos, em segundo
plano (:meth:`Memory.unembedded` / :meth:`Memory.store_vectors`); sem eles a
busca é só por palavra, como antes.

Os fatos também são escolhidos pelo assunto (:meth:`Memory.facts_for`): com
centenas guardados, ela vê os que importam para a conversa, não só os 15 últimos.

Episódios antigos não ficam para sempre: a consolidação
(:mod:`akemi.brain.consolidation`) troca os de um dia por resumos curtos
(``kind="resumo"``), com :meth:`Memory.old_episodes` e :meth:`Memory.replace_with_summary`.
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    text, content='episodes', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    text TEXT NOT NULL UNIQUE
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    text, content='facts', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
    DELETE FROM vectors WHERE owner = 'fact' AND ref = old.id;
END;
-- Vetores de significado: owner = 'ep' (episódio) ou 'fact'; model diz de qual
-- modelo veio (trocar de modelo recalcula tudo, vetores de modelos diferentes não se comparam).
CREATE TABLE IF NOT EXISTS vectors (
    owner TEXT NOT NULL,
    ref INTEGER NOT NULL,
    model TEXT NOT NULL,
    vec BLOB NOT NULL,
    PRIMARY KEY (owner, ref)
);
CREATE TRIGGER IF NOT EXISTS episodes_ad_vec AFTER DELETE ON episodes BEGIN
    DELETE FROM vectors WHERE owner = 'ep' AND ref = old.id;
END;
"""
SCHEMA_VERSION = 2  # 2: fatos com busca (facts_fts) e vetores

STOPWORDS = set(
    ["a", "o", "as", "os", "um", "uma", "de", "da", "do", "das", "dos", "e", "é", "em", "no", "na", "nos", "nas", "que", "pra", "para", "por", "com", "se", "eu", "tu", "ele", "ela", "você", "voce", "me", "te", "isso", "isto", "esse", "essa", "ai", "aí", "lá", "la", "ja", "já", "mas", "the", "and", "is", "are", "to", "of", "in", "it", "you"]
)


SUMMARY_KIND = "resumo"
RECENCY_HALF_LIFE_DAYS = 30.0  # uma lembrança de 30 dias pesa metade de uma de hoje no desempate
RECENCY_WEIGHT = 0.3  # quanto a idade conta perto da relevância (0 = só relevância)
SEMANTIC_WEIGHT = 0.6  # com vetor: quanto o significado pesa perto da palavra-chave
MIN_SIMILARITY = 0.35  # abaixo disso (cosseno), "parecido" é só ruído


@dataclass
class Recall:
    ts: float
    kind: str
    text: str


@dataclass
class StoredEpisode:
    id: int
    ts: float
    kind: str
    text: str


class Memory:
    def __init__(self, path: Path | str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._vec_cache: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
        with self._lock:
            self._db.executescript(SCHEMA)
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                # Banco de antes: os fatos já guardados entram na busca.
                self._db.execute("INSERT INTO facts_fts(facts_fts) VALUES ('rebuild')")
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._db.commit()

    def add_episode(self, kind: str, text: str, ts: float | None = None) -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO episodes(ts, kind, text) VALUES (?, ?, ?)",
                (ts or time.time(), kind, text[:2000]),
            )
            self._db.commit()

    def add_fact(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO facts(ts, text) VALUES (?, ?)", (time.time(), text[:500])
            )
            self._db.commit()
            return cur.rowcount > 0

    def forget_fact(self, contains: str) -> int:
        with self._lock:
            cur = self._db.execute("DELETE FROM facts WHERE text LIKE ?", (f"%{contains}%",))
            self._db.commit()
            return cur.rowcount

    def facts(self, limit: int = 30) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT text FROM facts ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [r[0] for r in rows]

    @staticmethod
    def _fts_query(text: str) -> str:
        words = [w for w in re.findall(r"\w{3,}", text.lower()) if w not in STOPWORDS]
        return " OR ".join(f'"{w}"' for w in dict.fromkeys(words[:12]))

    @staticmethod
    def _keyword_scores(rows: list[tuple]) -> dict[int, float]:
        """bm25 (negativo: menor = melhor) de cada linha normalizado para 0..1."""
        if not rows:
            return {}
        best, worst = rows[0][-1], rows[-1][-1]
        span = (worst - best) or 1.0
        return {r[0]: 1.0 - (r[-1] - best) / span for r in rows}

    def _semantic_scores(self, owner: str, query_vec, model: str, top: int) -> dict[int, float]:
        """Os ``top`` mais parecidos em significado (cosseno >= MIN_SIMILARITY)."""
        if query_vec is None or not model:
            return {}
        refs, matrix = self._vectors(owner, model)
        q = np.asarray(query_vec, dtype=np.float32)
        if not len(refs) or matrix.shape[1] != q.shape[0]:
            return {}
        sims = matrix @ q
        idx = np.argsort(-sims)[:top]
        return {int(refs[i]): float(sims[i]) for i in idx if sims[i] >= MIN_SIMILARITY}

    @staticmethod
    def _relevance(ref: int, kw: dict[int, float], sem: dict[int, float]) -> float:
        k = kw.get(ref, 0.0)
        if not sem:
            return k
        # Cosseno de MIN_SIMILARITY..1 vira 0..1 para ficar na mesma régua da palavra-chave.
        s = max(0.0, (sem.get(ref, 0.0) - MIN_SIMILARITY) / (1 - MIN_SIMILARITY))
        return SEMANTIC_WEIGHT * s + (1 - SEMANTIC_WEIGHT) * k

    def recall(
        self,
        query: str,
        limit: int = 4,
        exclude_recent_s: float = 0.0,
        *,
        query_vec=None,
        model: str = "",
    ) -> list[Recall]:
        """As lembranças mais relevantes; entre as parecidas, as mais novas ganham.

        Com ``query_vec`` (o vetor da pergunta, do mesmo ``model`` que indexou a
        memória), acha também o que tem o mesmo sentido em outras palavras.
        """
        q = self._fts_query(query)
        now = time.time()
        cutoff = now - exclude_recent_s
        pool = max(limit * 5, 20)
        kw_rows: list[tuple] = []
        with self._lock:
            if q:
                kw_rows = self._db.execute(
                    "SELECT e.id, bm25(episodes_fts) FROM episodes_fts f "
                    "JOIN episodes e ON e.id = f.rowid "
                    "WHERE episodes_fts MATCH ? AND e.ts <= ? ORDER BY bm25(episodes_fts) LIMIT ?",
                    (q, cutoff, pool),
                ).fetchall()
        kw = self._keyword_scores(kw_rows)
        sem = self._semantic_scores("ep", query_vec, model, pool)
        ids = list(dict.fromkeys([*kw, *sem]))
        if not ids:
            return []
        with self._lock:
            marks = ",".join("?" * len(ids))
            rows = self._db.execute(
                f"SELECT id, ts, kind, text FROM episodes WHERE id IN ({marks}) AND ts <= ?", (*ids, cutoff)
            ).fetchall()

        def score(row) -> float:
            age_days = max(0.0, now - row[1]) / 86400
            recency = math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)
            return (1 - RECENCY_WEIGHT) * self._relevance(row[0], kw, sem) + RECENCY_WEIGHT * recency

        ranked = sorted(rows, key=score, reverse=True)[:limit]
        return [Recall(ts, kind, text) for _, ts, kind, text in ranked]

    def facts_for(self, query: str = "", limit: int = 15, *, query_vec=None, model: str = "") -> list[str]:
        """Fatos para o prompt: os ligados ao assunto primeiro, o resto dos mais novos.

        Sem assunto (ou nada relacionado), é o mesmo que :meth:`facts`.
        """
        q = self._fts_query(query) if query else ""
        kw_rows: list[tuple] = []
        with self._lock:
            if q:
                kw_rows = self._db.execute(
                    "SELECT rowid, bm25(facts_fts) FROM facts_fts WHERE facts_fts MATCH ? "
                    "ORDER BY bm25(facts_fts) LIMIT ?",
                    (q, limit * 2),
                ).fetchall()
        kw = self._keyword_scores(kw_rows)
        sem = self._semantic_scores("fact", query_vec, model, limit * 2)
        related = sorted(set(kw) | set(sem), key=lambda r: self._relevance(r, kw, sem), reverse=True)
        related = related[: max(1, limit * 2 // 3)]  # sobra espaço para os mais novos
        with self._lock:
            chosen: list[str] = []
            if related:
                marks = ",".join("?" * len(related))
                by_id = dict(self._db.execute(
                    f"SELECT id, text FROM facts WHERE id IN ({marks})", related
                ).fetchall())
                chosen = [by_id[r] for r in related if r in by_id]
            newest = self._db.execute(
                "SELECT text FROM facts ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
        for (text,) in newest:
            if len(chosen) >= limit:
                break
            if text not in chosen:
                chosen.append(text)
        return chosen

    # ------------------------------------------------------------ vetores
    def unembedded(self, model: str, limit: int = 64) -> list[tuple[str, int, str]]:
        """O que ainda não tem vetor deste ``model``: ``(owner, ref, texto)``, fatos primeiro."""
        with self._lock:
            rows = self._db.execute(
                "SELECT 'fact', f.id, f.text FROM facts f LEFT JOIN vectors v "
                "ON v.owner = 'fact' AND v.ref = f.id AND v.model = ? WHERE v.ref IS NULL "
                "UNION ALL "
                "SELECT 'ep', e.id, e.text FROM episodes e LEFT JOIN vectors v "
                "ON v.owner = 'ep' AND v.ref = e.id AND v.model = ? WHERE v.ref IS NULL "
                "LIMIT ?",
                (model, model, limit),
            ).fetchall()
        return [(o, int(r), t) for o, r, t in rows]

    def store_vectors(self, model: str, items: list[tuple[str, int, str, np.ndarray]]) -> int:
        """Guarda ``(owner, ref, texto, vetor)`` (troca o de outro modelo, se houver).

        Ignora o que foi apagado ou trocado enquanto o vetor era calculado: o
        SQLite reaproveita ids, então confere o texto, não só o id.
        """
        saved = 0
        with self._lock:
            for owner, ref, text, vec in items:
                table = "facts" if owner == "fact" else "episodes"
                row = self._db.execute(f"SELECT text FROM {table} WHERE id = ?", (ref,)).fetchone()
                if row is None or row[0] != text:
                    continue
                self._db.execute(
                    "INSERT OR REPLACE INTO vectors(owner, ref, model, vec) VALUES (?, ?, ?, ?)",
                    (owner, ref, model, np.asarray(vec, dtype=np.float32).tobytes()),
                )
                saved += 1
            self._db.commit()
            self._vec_cache.clear()
        return saved

    def _vectors(self, owner: str, model: str) -> tuple[np.ndarray, np.ndarray]:
        """``(refs, matriz)`` com os vetores de ``owner``, em cache até mudar algo."""
        key = (owner, model)
        with self._lock:
            # Apagar episódio/fato tira o vetor por trigger: confere se o cache ainda vale.
            count = self._db.execute(
                "SELECT COUNT(*) FROM vectors WHERE owner = ? AND model = ?", (owner, model)
            ).fetchone()[0]
            cached = self._vec_cache.get(key)
            if cached is not None and len(cached[0]) == count:
                return cached
            rows = self._db.execute(
                "SELECT ref, vec FROM vectors WHERE owner = ? AND model = ?", (owner, model)
            ).fetchall()
        if not rows:
            result = (np.zeros(0, dtype=np.int64), np.zeros((0, 0), dtype=np.float32))
        else:
            dims = {len(v) for _, v in rows}
            if len(dims) != 1:  # não devia acontecer com o mesmo modelo; ignora os estranhos
                common = max(dims, key=lambda d: sum(len(v) == d for _, v in rows))
                rows = [r for r in rows if len(r[1]) == common]
            refs = np.array([r for r, _ in rows], dtype=np.int64)
            matrix = np.vstack([np.frombuffer(v, dtype=np.float32) for _, v in rows])
            result = (refs, matrix)
        with self._lock:
            self._vec_cache[key] = result
        return result

    def recent(self, limit: int = 10, kind: str | None = None, since: float = 0.0) -> list[Recall]:
        """Últimos episódios (do mais antigo ao mais novo), opcionalmente de um tipo."""
        sql, args = "SELECT ts, kind, text FROM episodes WHERE ts > ?", [since]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY ts DESC LIMIT ?", (*args, limit)).fetchall()
        return [Recall(*r) for r in reversed(rows)]

    # ------------------------------------------------------- consolidação
    def old_episodes(self, before: float, limit: int = 500) -> list[StoredEpisode]:
        """Episódios (fora os resumos) mais antigos que ``before``, do mais velho ao mais novo."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, ts, kind, text FROM episodes WHERE ts < ? AND kind != ? ORDER BY ts, id LIMIT ?",
                (before, SUMMARY_KIND, limit),
            ).fetchall()
        return [StoredEpisode(*r) for r in rows]

    def replace_with_summary(self, ids: list[int], summary: str, ts: float) -> None:
        """Apaga os episódios ``ids`` e guarda ``summary`` no lugar, tudo ou nada."""
        summary = summary.strip()
        if not ids or not summary:
            raise ValueError("resumo vazio ou sem episódios")
        with self._lock:
            try:
                self._db.execute("BEGIN")
                self._db.executemany("DELETE FROM episodes WHERE id = ?", [(i,) for i in ids])
                self._db.execute(
                    "INSERT INTO episodes(ts, kind, text) VALUES (?, ?, ?)", (ts, SUMMARY_KIND, summary[:2000])
                )
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise

    def stats(self) -> dict:
        """Quanto ela guarda: episódios por tipo, resumos, fatos e o mais antigo."""
        with self._lock:
            kinds = dict(self._db.execute("SELECT kind, COUNT(*) FROM episodes GROUP BY kind").fetchall())
            facts = self._db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            oldest = self._db.execute("SELECT MIN(ts) FROM episodes").fetchone()[0]
            vectors = self._db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        return {
            "com_vetor": vectors,
            "episodios": sum(n for k, n in kinds.items() if k != SUMMARY_KIND),
            "resumos": kinds.get(SUMMARY_KIND, 0),
            "por_tipo": kinds,
            "fatos": facts,
            "mais_antigo": oldest,
        }

    def backup_to(self, path: Path | str) -> None:
        """Cópia consistente do banco (funciona com ela rodando)."""
        dest = sqlite3.connect(str(path))
        try:
            with self._lock:
                self._db.backup(dest)
        finally:
            dest.close()

    def close(self) -> None:
        with self._lock:
            self._db.close()
