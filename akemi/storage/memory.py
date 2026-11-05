"""Memória de longo prazo em SQLite.

- ``episodes``: o que aconteceu (conversas, coisas vistas), com busca FTS5.
- ``facts``: coisas que o dono pediu para lembrar ou que são estáveis.

Busca por palavra-chave por enquanto; embeddings podem entrar depois sem
mudar a interface (``recall``).
"""

from __future__ import annotations

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
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    text TEXT NOT NULL UNIQUE
);
"""

STOPWORDS = set(
    "a o as os um uma de da do das dos e é em no na nos nas que pra para por com "
    "se eu tu ele ela você voce me te isso isto esse essa ai aí lá la ja já mas "
    "the and is are to of in it you".split()
)


@dataclass
class Recall:
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
        q = self._fts_query(query)
        if not q:
            return []
        cutoff = time.time() - exclude_recent_s
        with self._lock:
            rows = self._db.execute(
                "SELECT e.ts, e.kind, e.text FROM episodes_fts f JOIN episodes e ON e.id = f.rowid "
                "WHERE episodes_fts MATCH ? AND e.ts <= ? ORDER BY bm25(episodes_fts) LIMIT ?",
                (q, cutoff, limit),
            ).fetchall()
        return [Recall(*r) for r in rows]

    def recent(self, limit: int = 10, kind: str | None = None, since: float = 0.0) -> list[Recall]:
        """Últimos episódios (do mais antigo ao mais novo), opcionalmente de um tipo."""
        sql, args = "SELECT ts, kind, text FROM episodes WHERE ts > ?", [since]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY ts DESC LIMIT ?", (*args, limit)).fetchall()
        return [Recall(*r) for r in reversed(rows)]

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
