"""Memória de longo prazo em SQLite.

- ``episodes``: o que aconteceu (conversas, coisas vistas), com busca FTS5.
- ``facts``: coisas que o dono pediu para lembrar ou que são estáveis.

Busca por palavra-chave (FTS5/bm25) com um empurrão para o que é recente;
embeddings podem entrar depois sem mudar a interface (``recall``).

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
"""

STOPWORDS = set(
    ["a", "o", "as", "os", "um", "uma", "de", "da", "do", "das", "dos", "e", "é", "em", "no", "na", "nos", "nas", "que", "pra", "para", "por", "com", "se", "eu", "tu", "ele", "ela", "você", "voce", "me", "te", "isso", "isto", "esse", "essa", "ai", "aí", "lá", "la", "ja", "já", "mas", "the", "and", "is", "are", "to", "of", "in", "it", "you"]
)


SUMMARY_KIND = "resumo"
RECENCY_HALF_LIFE_DAYS = 30.0  # uma lembrança de 30 dias pesa metade de uma de hoje no desempate
RECENCY_WEIGHT = 0.3  # quanto a idade conta perto da relevância (0 = só relevância)


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
        with self._lock:
            self._db.executescript(SCHEMA)

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

    def recall(self, query: str, limit: int = 4, exclude_recent_s: float = 0.0) -> list[Recall]:
        """As lembranças mais relevantes; entre as parecidas, as mais novas ganham."""
        q = self._fts_query(query)
        if not q:
            return []
        now = time.time()
        cutoff = now - exclude_recent_s
        with self._lock:
            rows = self._db.execute(
                "SELECT e.ts, e.kind, e.text, bm25(episodes_fts) FROM episodes_fts f "
                "JOIN episodes e ON e.id = f.rowid "
                "WHERE episodes_fts MATCH ? AND e.ts <= ? ORDER BY bm25(episodes_fts) LIMIT ?",
                (q, cutoff, max(limit * 5, 20)),
            ).fetchall()
        if not rows:
            return []
        # bm25 é negativo: quanto menor, mais relevante. Normaliza para 0..1.
        best, worst = rows[0][3], rows[-1][3]
        span = (worst - best) or 1.0

        def score(row) -> float:
            relevance = 1.0 - (row[3] - best) / span
            age_days = max(0.0, now - row[0]) / 86400
            recency = math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)
            return (1 - RECENCY_WEIGHT) * relevance + RECENCY_WEIGHT * recency

        ranked = sorted(rows, key=score, reverse=True)[:limit]
        return [Recall(ts, kind, text) for ts, kind, text, _ in ranked]

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
        return {
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
