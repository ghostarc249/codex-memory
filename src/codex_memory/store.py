from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

VALID_TYPES = {"decision", "constraint", "pattern", "task_context", "pitfall", "note"}


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def default_db_path() -> Path:
    return Path(os.environ.get("CODEX_MEMORY_DB", codex_home() / "codex-memory" / "memory.db")).expanduser()


@dataclass
class Memory:
    id: int
    scope: str
    type: str
    title: str
    content: str
    tags: list[str]
    source: str | None
    created_at: str
    updated_at: str


class MemoryStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    source TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    title,
                    content,
                    tags,
                    content='memories',
                    content_rowid='id'
                );
            """)
            conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, title, content, tags)
                    VALUES (new.id, new.title, new.content, new.tags);
                END;

                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
                    VALUES('delete', old.id, old.title, old.content, old.tags);
                END;

                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
                    VALUES('delete', old.id, old.title, old.content, old.tags);
                    INSERT INTO memories_fts(rowid, title, content, tags)
                    VALUES (new.id, new.title, new.content, new.tags);
                END;
            """)

    def add(self, scope: str, type_: str, title: str, content: str, tags: list[str] | None = None, source: str | None = None) -> dict[str, Any]:
        if type_ not in VALID_TYPES:
            raise ValueError(f"Invalid memory type: {type_}. Valid types: {sorted(VALID_TYPES)}")
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO memories(scope, type, title, content, tags, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (scope, type_, title, content, json.dumps(tags or []), source),
            )
            row_id = int(cur.lastrowid)
        return self.get(row_id)

    def get(self, id_: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (id_,)).fetchone()
            if not row:
                raise KeyError(f"Memory not found: {id_}")
            return self._row_to_dict(row)

    def list(self, scope: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if scope:
                rows = conn.execute(
                    "SELECT * FROM memories WHERE scope = ? ORDER BY updated_at DESC, id DESC LIMIT ?",
                    (scope, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM memories ORDER BY updated_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def search(self, query: str, scope: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return self.list(scope=scope, limit=limit)

        # Basic escaping for FTS5. Convert words into AND query.
        tokens = [t.replace('"', '""') for t in query.split() if t.strip()]
        fts_query = " AND ".join(f'"{t}"' for t in tokens)

        sql = """
            SELECT m.*, bm25(memories_fts) AS score
            FROM memories_fts
            JOIN memories m ON m.id = memories_fts.rowid
            WHERE memories_fts MATCH ?
        """
        params: list[Any] = [fts_query]
        if scope:
            sql += " AND m.scope = ?"
            params.append(scope)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r, include_score=True) for r in rows]

    def forget(self, id_: int) -> dict[str, Any]:
        existing = self.get(id_)
        with self.connect() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (id_,))
        return existing

    def health(self) -> dict[str, Any]:
        with self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
            fts_count = conn.execute("SELECT COUNT(*) AS c FROM memories_fts").fetchone()["c"]
            sqlite_version = conn.execute("SELECT sqlite_version() AS v").fetchone()["v"]
        return {
            "ok": True,
            "db_path": str(self.db_path),
            "memory_count": count,
            "fts_count": fts_count,
            "sqlite_version": sqlite_version,
        }

    @staticmethod
    def _row_to_dict(row: sqlite3.Row, include_score: bool = False) -> dict[str, Any]:
        result = {
            "id": row["id"],
            "scope": row["scope"],
            "type": row["type"],
            "title": row["title"],
            "content": row["content"],
            "tags": json.loads(row["tags"] or "[]"),
            "source": row["source"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if include_score and "score" in row.keys():
            result["score"] = row["score"]
        return result
