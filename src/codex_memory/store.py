from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .embeddings import DEFAULT_EMBEDDING_DIMENSIONS, DEFAULT_EMBEDDING_PROVIDER, cosine_similarity, embed_text

VALID_TYPES = {"decision", "constraint", "pattern", "task_context", "pitfall", "note"}
SCHEMA_VERSION = 3


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
        conn = sqlite3.connect(str(self.db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
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
                CREATE TABLE IF NOT EXISTS memory_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    tool_name TEXT,
                    source TEXT,
                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(scope, file_path)
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS invocations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    exit_code INTEGER NOT NULL,
                    scope TEXT,
                    rows_returned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    memory_id INTEGER PRIMARY KEY,
                    provider TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
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
            conn.execute(
                """
                INSERT INTO metadata(key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

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
            self._upsert_embedding(conn, row_id, title, content, tags or [])
        return self.get(row_id)

    def get(self, id_: int) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (id_,)).fetchone()
            if not row:
                raise KeyError(f"Memory not found: {id_}")
            return self._row_to_dict(row)

    def list(self, scope: str | None = None, limit: int = 20, days: int | None = None, type_: str | None = None) -> list[dict[str, Any]]:
        where, params = self._memory_filters(scope=scope, days=days, type_=type_)
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memories {where} ORDER BY updated_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def search(self, query: str, scope: str | None = None, limit: int = 10, days: int | None = None) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return self.list(scope=scope, limit=limit, days=days)

        fts_query = self._fts_query(query)

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
        if days is not None:
            sql += " AND m.updated_at >= datetime('now', ?)"
            params.append(f"-{days} days")
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r, include_score=True) for r in rows]

    def semantic_search(self, query: str, scope: str | None = None, limit: int = 10, days: int | None = None) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return self.list(scope=scope, limit=limit, days=days)

        query_vector = embed_text(query)
        where, params = self._memory_filters(scope=scope, days=days)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT m.*, e.provider, e.dimensions, e.vector
                FROM memories m
                JOIN memory_embeddings e ON e.memory_id = m.id
                {where}
                """,
                params,
            ).fetchall()
        scored: list[dict[str, Any]] = []
        for row in rows:
            memory = self._row_to_dict(row)
            memory["semantic_score"] = cosine_similarity(query_vector, json.loads(row["vector"]))
            memory["embedding_provider"] = row["provider"]
            scored.append(memory)
        scored.sort(key=lambda item: item["semantic_score"], reverse=True)
        return scored[:limit]

    def hybrid_search(self, query: str, scope: str | None = None, limit: int = 10, days: int | None = None) -> list[dict[str, Any]]:
        fts_results = self.search(query, scope=scope, limit=max(limit, 10), days=days)
        semantic_results = self.semantic_search(query, scope=scope, limit=max(limit, 10), days=days)
        merged: dict[int, dict[str, Any]] = {}

        for rank, memory in enumerate(fts_results):
            item = dict(memory)
            item["fts_rank"] = rank + 1
            item["hybrid_score"] = 1.0 / (rank + 1)
            merged[item["id"]] = item

        for rank, memory in enumerate(semantic_results):
            item = merged.setdefault(memory["id"], dict(memory))
            item["semantic_rank"] = rank + 1
            item["semantic_score"] = memory.get("semantic_score", 0.0)
            item["hybrid_score"] = item.get("hybrid_score", 0.0) + max(0.0, float(memory.get("semantic_score", 0.0)))

        results = list(merged.values())
        results.sort(key=lambda item: item.get("hybrid_score", 0.0), reverse=True)
        return results[:limit]

    def forget(self, id_: int) -> dict[str, Any]:
        existing = self.get(id_)
        with self.connect() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (id_,))
        return existing

    def rebuild_embeddings(self, scope: str | None = None) -> dict[str, Any]:
        where, params = self._memory_filters(scope=scope)
        with self.connect() as conn:
            rows = conn.execute(f"SELECT * FROM memories {where}", params).fetchall()
            for row in rows:
                tags = json.loads(row["tags"] or "[]")
                self._upsert_embedding(conn, int(row["id"]), row["title"], row["content"], tags)
        return {
            "ok": True,
            "scope": scope,
            "rebuilt": len(rows),
            "provider": DEFAULT_EMBEDDING_PROVIDER,
            "dimensions": DEFAULT_EMBEDDING_DIMENSIONS,
        }

    def record_file(self, scope: str, file_path: str, tool_name: str | None = None, source: str | None = None) -> dict[str, Any]:
        normalized = file_path.strip()
        if not normalized:
            raise ValueError("file_path is required")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_files(scope, file_path, tool_name, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(scope, file_path) DO UPDATE SET
                    tool_name = excluded.tool_name,
                    source = COALESCE(excluded.source, memory_files.source),
                    last_seen_at = CURRENT_TIMESTAMP,
                    seen_count = memory_files.seen_count + 1
                """,
                (scope, normalized, tool_name, source),
            )
            row = conn.execute(
                "SELECT * FROM memory_files WHERE scope = ? AND file_path = ?",
                (scope, normalized),
            ).fetchone()
        return self._file_row_to_dict(row)

    def list_files(self, scope: str | None = None, limit: int = 10, days: int | None = None) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        params: list[Any] = []
        if scope:
            where_parts.append("scope = ?")
            params.append(scope)
        if days is not None:
            where_parts.append("last_seen_at >= datetime('now', ?)")
            params.append(f"-{days} days")
        where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM memory_files {where} ORDER BY last_seen_at DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._file_row_to_dict(r) for r in rows]

    def checkpoints(self, scope: str | None = None, limit: int = 5, days: int | None = None) -> list[dict[str, Any]]:
        return self.list(scope=scope, limit=limit, days=days, type_="task_context")

    def record_invocation(self, command: str, duration_ms: int, exit_code: int = 0, scope: str | None = None, rows_returned: int = 0) -> None:
        try:
            with self.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO invocations(command, duration_ms, exit_code, scope, rows_returned)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (command, duration_ms, exit_code, scope, rows_returned),
                )
                stale_id = conn.execute(
                    "SELECT id FROM invocations ORDER BY id DESC LIMIT 1 OFFSET 500"
                ).fetchone()
                if stale_id:
                    conn.execute("DELETE FROM invocations WHERE id <= ?", (stale_id["id"],))
        except Exception:
            pass

    def schema_check(self) -> list[str]:
        expected: dict[str, set[str]] = {
            "metadata": {"key", "value"},
            "memories": {"id", "scope", "type", "title", "content", "tags", "source", "created_at", "updated_at"},
            "memory_embeddings": {"memory_id", "provider", "dimensions", "vector", "updated_at"},
            "memory_files": {"id", "scope", "file_path", "tool_name", "source", "first_seen_at", "last_seen_at", "seen_count"},
            "invocations": {"id", "command", "duration_ms", "exit_code", "scope", "rows_returned", "created_at"},
        }
        problems: list[str] = []
        with self.connect() as conn:
            for table, columns in expected.items():
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                if not rows:
                    problems.append(f"MISSING TABLE: {table}")
                    continue
                actual = {r["name"] for r in rows}
                missing = columns - actual
                if missing:
                    problems.append(f"{table}: missing columns {sorted(missing)}")
            fts = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memories_fts'").fetchone()
            if not fts:
                problems.append("MISSING FTS TABLE: memories_fts")
            version = conn.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
            if not version:
                problems.append("metadata: missing schema_version")
            memory_count = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
            embedding_count = conn.execute("SELECT COUNT(*) AS c FROM memory_embeddings").fetchone()["c"]
            if memory_count != embedding_count:
                problems.append(f"memory_embeddings: expected {memory_count} vectors, found {embedding_count}; run `codex-memory embeddings rebuild`")
        return problems

    def health(self) -> dict[str, Any]:
        started = time.monotonic()
        with self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
            fts_count = conn.execute("SELECT COUNT(*) AS c FROM memories_fts").fetchone()["c"]
            embedding_count = conn.execute("SELECT COUNT(*) AS c FROM memory_embeddings").fetchone()["c"]
            file_count = conn.execute("SELECT COUNT(*) AS c FROM memory_files").fetchone()["c"]
            invocation_count = conn.execute("SELECT COUNT(*) AS c FROM invocations").fetchone()["c"]
            sqlite_version = conn.execute("SELECT sqlite_version() AS v").fetchone()["v"]
            latest = conn.execute("SELECT MAX(updated_at) AS latest FROM memories").fetchone()["latest"]
            scoped = conn.execute("SELECT COUNT(DISTINCT scope) AS c FROM memories").fetchone()["c"]
            failed_invocations = conn.execute("SELECT COUNT(*) AS c FROM invocations WHERE exit_code != 0").fetchone()["c"]
        latency_ms = int((time.monotonic() - started) * 1000)
        schema_problems = self.schema_check()
        dimensions = [
            self._dimension("schema_integrity", not schema_problems, "All expected tables/columns OK" if not schema_problems else "; ".join(schema_problems)),
            self._dimension("query_latency", latency_ms <= 100, f"{latency_ms}ms"),
            self._dimension("corpus_size", count > 0, f"{count} memories"),
            self._dimension("fts_integrity", count == fts_count, f"{fts_count}/{count} FTS rows"),
            self._dimension("embedding_coverage", count == embedding_count, f"{embedding_count}/{count} vectors"),
            self._dimension("file_recall", file_count > 0, f"{file_count} tracked files"),
            self._dimension("scope_coverage", scoped > 0, f"{scoped} scopes"),
            self._dimension("invocation_telemetry", invocation_count > 0, f"{invocation_count} invocations, {failed_invocations} failed"),
        ]
        return {
            "ok": not schema_problems,
            "db_path": str(self.db_path),
            "schema_version": SCHEMA_VERSION,
            "memory_count": count,
            "fts_count": fts_count,
            "embedding_count": embedding_count,
            "embedding_provider": DEFAULT_EMBEDDING_PROVIDER,
            "embedding_dimensions": DEFAULT_EMBEDDING_DIMENSIONS,
            "file_count": file_count,
            "invocation_count": invocation_count,
            "latest_memory_at": latest,
            "query_latency_ms": latency_ms,
            "sqlite_version": sqlite_version,
            "schema_problems": schema_problems,
            "dimensions": dimensions,
        }

    @staticmethod
    def _memory_filters(scope: str | None = None, days: int | None = None, type_: str | None = None) -> tuple[str, list[Any]]:
        where_parts: list[str] = []
        params: list[Any] = []
        if scope:
            where_parts.append("scope = ?")
            params.append(scope)
        if days is not None:
            where_parts.append("updated_at >= datetime('now', ?)")
            params.append(f"-{days} days")
        if type_:
            where_parts.append("type = ?")
            params.append(type_)
        where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
        return where, params

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = [t.replace('"', '""') for t in query.split() if t.strip()]
        return " AND ".join(f'"{t}"' for t in tokens)

    @staticmethod
    def _upsert_embedding(conn: sqlite3.Connection, memory_id: int, title: str, content: str, tags: list[str]) -> None:
        vector = embed_text(" ".join([title, content, " ".join(tags)]))
        conn.execute(
            """
            INSERT INTO memory_embeddings(memory_id, provider, dimensions, vector, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(memory_id) DO UPDATE SET
                provider = excluded.provider,
                dimensions = excluded.dimensions,
                vector = excluded.vector,
                updated_at = CURRENT_TIMESTAMP
            """,
            (memory_id, DEFAULT_EMBEDDING_PROVIDER, DEFAULT_EMBEDDING_DIMENSIONS, json.dumps(vector)),
        )

    @staticmethod
    def _dimension(name: str, ok: bool, detail: str) -> dict[str, Any]:
        return {
            "name": name,
            "zone": "GREEN" if ok else "YELLOW",
            "score": 10 if ok else 0,
            "detail": detail,
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

    @staticmethod
    def _file_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "scope": row["scope"],
            "file_path": row["file_path"],
            "tool_name": row["tool_name"],
            "source": row["source"],
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "seen_count": row["seen_count"],
        }
