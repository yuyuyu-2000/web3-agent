from __future__ import annotations

from dataclasses import dataclass

import pytest

from chaincloud_agent_service.memory.factory import create_memory_store
from chaincloud_agent_service.memory.store import (
    InMemoryMemoryStore,
    PostgresMemoryStore,
)


@dataclass
class DummySettings:
    memory_store_backend: str = "memory"
    memory_database_url: str | None = None
    memory_postgres_table: str = "agent_memories"
    memory_postgres_auto_create: bool = False


def test_create_memory_store_defaults_to_in_memory() -> None:
    store = create_memory_store(DummySettings())

    assert isinstance(store, InMemoryMemoryStore)


def test_create_memory_store_accepts_memory_alias() -> None:
    store = create_memory_store(DummySettings(memory_store_backend="in_memory"))

    assert isinstance(store, InMemoryMemoryStore)


def test_create_memory_store_requires_database_url_for_postgres() -> None:
    with pytest.raises(RuntimeError, match="MEMORY_DATABASE_URL"):
        create_memory_store(DummySettings(memory_store_backend="postgres"))


def test_create_memory_store_rejects_unknown_backend() -> None:
    with pytest.raises(RuntimeError, match="Unsupported MEMORY_STORE_BACKEND"):
        create_memory_store(DummySettings(memory_store_backend="redis"))


def test_create_memory_store_creates_postgres_store_without_connecting_when_auto_create_is_false() -> (
    None
):
    store = create_memory_store(
        DummySettings(
            memory_store_backend="postgres",
            memory_database_url="postgresql://user:password@localhost:5432/chaincloud",
            memory_postgres_table="agent_memories_test",
            memory_postgres_auto_create=False,
        )
    )

    assert isinstance(store, PostgresMemoryStore)
    assert store.database_url == "postgresql://user:password@localhost:5432/chaincloud"
    assert store.table_name == "agent_memories_test"
