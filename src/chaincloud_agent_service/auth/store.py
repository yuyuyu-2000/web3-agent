"""User stores for the PostgreSQL-backed login MVP."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    username: str
    password_hash: str
    display_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_login_at: datetime | None = None


class UserStore(Protocol):
    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UserRecord: ...

    def get_user_by_username(self, username: str) -> UserRecord | None: ...

    def get_user_by_id(self, user_id: str) -> UserRecord | None: ...

    def update_last_login(self, user_id: str) -> UserRecord | None: ...


def normalize_username(username: str) -> str:
    return username.strip().lower()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validate_table_name(table_name: str) -> str:
    if not _IDENTIFIER_RE.match(table_name):
        raise ValueError(f"invalid table name: {table_name!r}")
    return table_name


def _row_to_user(row: dict[str, Any] | None) -> UserRecord | None:
    if row is None:
        return None

    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}

    return UserRecord(
        user_id=str(row["user_id"]),
        username=str(row["username"]),
        password_hash=str(row["password_hash"]),
        display_name=row.get("display_name"),
        metadata=dict(metadata),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        last_login_at=row.get("last_login_at"),
    )


class InMemoryUserStore:
    def __init__(self) -> None:
        self._by_id: dict[str, UserRecord] = {}
        self._by_username: dict[str, str] = {}

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UserRecord:
        username = normalize_username(username)
        if username in self._by_username:
            raise ValueError("username already exists")

        user_id = str(uuid.uuid4())
        now = _now()
        user = UserRecord(
            user_id=user_id,
            username=username,
            password_hash=password_hash,
            display_name=display_name,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
            last_login_at=None,
        )
        self._by_id[user_id] = user
        self._by_username[username] = user_id
        return user

    def get_user_by_username(self, username: str) -> UserRecord | None:
        user_id = self._by_username.get(normalize_username(username))
        if user_id is None:
            return None
        return self._by_id.get(user_id)

    def get_user_by_id(self, user_id: str) -> UserRecord | None:
        return self._by_id.get(user_id)

    def update_last_login(self, user_id: str) -> UserRecord | None:
        user = self._by_id.get(user_id)
        if user is None:
            return None

        updated = UserRecord(
            user_id=user.user_id,
            username=user.username,
            password_hash=user.password_hash,
            display_name=user.display_name,
            metadata=user.metadata,
            created_at=user.created_at,
            updated_at=_now(),
            last_login_at=_now(),
        )
        self._by_id[user_id] = updated
        return updated


class PostgresUserStore:
    def __init__(
        self,
        database_url: str,
        *,
        table_name: str = "agent_users",
        auto_create: bool = False,
    ) -> None:
        self.database_url = database_url
        self.table_name = _validate_table_name(table_name)
        if auto_create:
            self.ensure_schema()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def ensure_schema(self) -> None:
        query = f"""
        CREATE TABLE IF NOT EXISTS {self.table_name} (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ
        );

        CREATE INDEX IF NOT EXISTS idx_{self.table_name}_username
        ON {self.table_name} (username);
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
            conn.commit()

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UserRecord:
        username = normalize_username(username)
        user_id = str(uuid.uuid4())

        query = f"""
        INSERT INTO {self.table_name} (
            user_id,
            username,
            password_hash,
            display_name,
            metadata
        )
        VALUES (%s, %s, %s, %s, %s)
        RETURNING
            user_id,
            username,
            password_hash,
            display_name,
            metadata,
            created_at,
            updated_at,
            last_login_at
        """
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        query,
                        (
                            user_id,
                            username,
                            password_hash,
                            display_name,
                            Json(metadata or {}),
                        ),
                    )
                    row = cur.fetchone()
                conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            raise ValueError("username already exists") from exc

        user = _row_to_user(row)
        if user is None:
            raise RuntimeError("failed to create user")
        return user

    def get_user_by_username(self, username: str) -> UserRecord | None:
        query = f"""
        SELECT
            user_id,
            username,
            password_hash,
            display_name,
            metadata,
            created_at,
            updated_at,
            last_login_at
        FROM {self.table_name}
        WHERE username = %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (normalize_username(username),))
                row = cur.fetchone()
        return _row_to_user(row)

    def get_user_by_id(self, user_id: str) -> UserRecord | None:
        query = f"""
        SELECT
            user_id,
            username,
            password_hash,
            display_name,
            metadata,
            created_at,
            updated_at,
            last_login_at
        FROM {self.table_name}
        WHERE user_id = %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (user_id,))
                row = cur.fetchone()
        return _row_to_user(row)

    def update_last_login(self, user_id: str) -> UserRecord | None:
        query = f"""
        UPDATE {self.table_name}
        SET last_login_at = now(), updated_at = now()
        WHERE user_id = %s
        RETURNING
            user_id,
            username,
            password_hash,
            display_name,
            metadata,
            created_at,
            updated_at,
            last_login_at
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (user_id,))
                row = cur.fetchone()
            conn.commit()
        return _row_to_user(row)


def create_user_store(settings: Any) -> UserStore:
    database_url = getattr(settings, "auth_database_url", None)
    table_name = getattr(settings, "auth_users_table", "agent_users")
    auto_create = bool(getattr(settings, "auth_postgres_auto_create", False))

    if database_url:
        return PostgresUserStore(
            database_url,
            table_name=table_name,
            auto_create=auto_create,
        )
    return InMemoryUserStore()
