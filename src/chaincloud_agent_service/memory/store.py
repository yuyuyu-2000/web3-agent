from __future__ import annotations

from typing import Any, Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from chaincloud_agent_service.memory.models import (
    MemoryCandidate,
    MemoryRecord,
    utc_now,
)


class MemoryStore(Protocol):
    """Storage interface used by MemoryService.

    Implementations can keep data in process memory or persist it to external
    storage such as PostgreSQL. Route and service layers should depend on this
    protocol instead of concrete storage classes.
    """

    def save(
        self,
        *,
        memory_key: str,
        summary: str,
        source_thread_id: str,
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
        memory_type: str | None = None,
        embedding: list[float] | None = None,
    ) -> MemoryRecord: ...

    def get(self, memory_key: str) -> MemoryRecord | None: ...

    def list(self) -> list[MemoryRecord]: ...

    def delete(self, memory_key: str) -> bool: ...

    def clear(self) -> None: ...

    def search(
        self, *, user_id: str, embedding: list[float], limit: int
    ) -> list[MemoryCandidate]: ...


class InMemoryMemoryStore:
    """Memory v1 的内存版存储。"""

    def __init__(self) -> None:
        self._store: dict[str, MemoryRecord] = {}

    def save(
        self,
        *,
        memory_key: str,
        summary: str,
        source_thread_id: str,
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
        memory_type: str | None = None,
        embedding: list[float] | None = None,
    ) -> MemoryRecord:
        existing = self._store.get(memory_key)
        now = utc_now()
        record = MemoryRecord(
            memory_key=memory_key,
            summary=summary,
            source_thread_id=source_thread_id,
            metadata=metadata or {},
            user_id=user_id or (metadata or {}).get("user_id"),
            memory_type=memory_type,
            embedding=embedding,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._store[memory_key] = record
        return record

    def get(self, memory_key: str) -> MemoryRecord | None:
        return self._store.get(memory_key)

    def list(self) -> list[MemoryRecord]:
        return sorted(
            self._store.values(),
            key=lambda record: record.updated_at,
            reverse=True,
        )

    def delete(self, memory_key: str) -> bool:
        if memory_key not in self._store:
            return False

        del self._store[memory_key]
        return True

    def clear(self) -> None:
        self._store.clear()

    def search(
        self, *, user_id: str, embedding: list[float], limit: int
    ) -> list[MemoryCandidate]:
        import math

        def cosine(other: list[float]) -> float:
            if len(other) != len(embedding) or not other:
                return 0.0
            denom = math.sqrt(sum(x * x for x in embedding)) * math.sqrt(
                sum(x * x for x in other)
            )
            return (
                sum(a * b for a, b in zip(embedding, other)) / denom if denom else 0.0
            )

        rows = []
        for record in self._store.values():
            owner = record.user_id or record.metadata.get("user_id")
            if owner == user_id and record.embedding:
                similarity = cosine(record.embedding)
                rows.append(
                    MemoryCandidate(
                        record=record, similarity=similarity, final_score=similarity
                    )
                )
        return sorted(rows, key=lambda item: item.final_score, reverse=True)[:limit]


class PostgresMemoryStore:
    """PostgreSQL-backed persistent memory store.

    This implementation keeps the same synchronous interface as
    InMemoryMemoryStore so existing route and service code does not need to know
    where memories are stored.

    It opens short-lived psycopg connections per operation. This keeps the first
    persistent version simple and easy to deploy. A connection pool can be
    introduced later without changing MemoryService or API routes.
    """

    def __init__(
        self,
        *,
        database_url: str,
        table_name: str = "agent_memories",
        auto_create: bool = False,
    ) -> None:
        database_url = database_url.strip()
        table_name = table_name.strip()

        if not database_url:
            raise ValueError("database_url is required for PostgresMemoryStore")
        if not table_name:
            raise ValueError("table_name is required for PostgresMemoryStore")

        self.database_url = database_url
        self.table_name = table_name

        if auto_create:
            self.create_table_if_not_exists()

    def _table(self) -> sql.Identifier:
        return sql.Identifier(self.table_name)

    def create_table_if_not_exists(self) -> None:
        query = sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {table} (
                memory_key TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                source_thread_id TEXT NOT NULL,
                metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        ).format(table=self._table())

        index_thread_id = sql.SQL(
            "CREATE INDEX IF NOT EXISTS {index} ON {table} (source_thread_id)"
        ).format(
            index=sql.Identifier(f"idx_{self.table_name}_source_thread_id"),
            table=self._table(),
        )
        index_updated_at = sql.SQL(
            "CREATE INDEX IF NOT EXISTS {index} ON {table} (updated_at DESC)"
        ).format(
            index=sql.Identifier(f"idx_{self.table_name}_updated_at"),
            table=self._table(),
        )

        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                cur.execute(index_thread_id)
                cur.execute(index_updated_at)

    def migrate_for_semantic_recall(self) -> None:
        """Add nullable recall columns; safe for existing rows and repeatable."""
        statements = [
            "CREATE EXTENSION IF NOT EXISTS vector",
            "ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id TEXT",
            "ALTER TABLE {table} ADD COLUMN IF NOT EXISTS memory_type TEXT",
            "ALTER TABLE {table} ADD COLUMN IF NOT EXISTS embedding vector",
            "CREATE INDEX IF NOT EXISTS {index} ON {table} (user_id)",
        ]
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(
                        sql.SQL(statement).format(
                            table=self._table(),
                            index=sql.Identifier(f"idx_{self.table_name}_user_id"),
                        )
                    )

    def _row_to_record(self, row: dict[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            memory_key=row["memory_key"],
            summary=row["summary"],
            source_thread_id=row["source_thread_id"],
            metadata=row.get("metadata") or {},
            user_id=row.get("user_id"),
            memory_type=row.get("memory_type"),
            embedding=list(row["embedding"])
            if row.get("embedding") is not None
            else None,
            created_at=row.get("created_at") or row["updated_at"],
            updated_at=row["updated_at"],
        )

    def save(
        self,
        *,
        memory_key: str,
        summary: str,
        source_thread_id: str,
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
        memory_type: str | None = None,
        embedding: list[float] | None = None,
    ) -> MemoryRecord:
        updated_at = utc_now()
        query = sql.SQL(
            """
            INSERT INTO {table} (memory_key, summary, source_thread_id, metadata, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (memory_key) DO UPDATE SET
                summary = EXCLUDED.summary,
                source_thread_id = EXCLUDED.source_thread_id,
                metadata = EXCLUDED.metadata,
                updated_at = EXCLUDED.updated_at
            RETURNING memory_key, summary, source_thread_id, metadata, updated_at
            """
        ).format(table=self._table())

        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        memory_key,
                        summary,
                        source_thread_id,
                        Jsonb(metadata or {}),
                        updated_at,
                    ),
                )
                row = cur.fetchone()

        if row is None:
            raise RuntimeError("failed to save memory record")
        if user_id is not None or memory_type is not None or embedding is not None:
            try:
                update = sql.SQL("""
                    UPDATE {table}
                    SET user_id = %s, memory_type = %s, embedding = %s::vector
                    WHERE memory_key = %s
                """).format(table=self._table())
                with psycopg.connect(self.database_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            update,
                            (
                                user_id,
                                memory_type,
                                str(embedding) if embedding else None,
                                memory_key,
                            ),
                        )
            except psycopg.Error:
                # An old schema or unavailable pgvector must not break v1 writes.
                pass
        return self._row_to_record(row)

    def search(
        self, *, user_id: str, embedding: list[float], limit: int
    ) -> list[MemoryCandidate]:
        query = sql.SQL("""
            SELECT memory_key, summary, source_thread_id, metadata, user_id,
                   memory_type, embedding, created_at, updated_at,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM {table}
            WHERE user_id = %s AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """).format(table=self._table())
        vector = str(embedding)
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (vector, user_id, vector, limit))
                rows = cur.fetchall()
        return [
            MemoryCandidate(
                record=self._row_to_record(row),
                similarity=float(row["similarity"]),
                final_score=float(row["similarity"]),
            )
            for row in rows
        ]

    def get(self, memory_key: str) -> MemoryRecord | None:
        query = sql.SQL(
            """
            SELECT memory_key, summary, source_thread_id, metadata, updated_at
            FROM {table}
            WHERE memory_key = %s
            """
        ).format(table=self._table())

        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (memory_key,))
                row = cur.fetchone()

        if row is None:
            return None
        return self._row_to_record(row)

    def list(self) -> list[MemoryRecord]:
        query = sql.SQL(
            """
            SELECT memory_key, summary, source_thread_id, metadata, updated_at
            FROM {table}
            ORDER BY updated_at DESC
            """
        ).format(table=self._table())

        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()

        return [self._row_to_record(row) for row in rows]

    def delete(self, memory_key: str) -> bool:
        query = sql.SQL("DELETE FROM {table} WHERE memory_key = %s").format(
            table=self._table()
        )

        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (memory_key,))
                return cur.rowcount > 0

    def clear(self) -> None:
        query = sql.SQL("DELETE FROM {table}").format(table=self._table())

        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
