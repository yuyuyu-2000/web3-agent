from __future__ import annotations

from typing import Any

from chaincloud_agent_service.memory.store import (
    InMemoryMemoryStore,
    MemoryStore,
    PostgresMemoryStore,
)


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def create_memory_store(settings: Any) -> MemoryStore:
    """Create the configured memory store.

    The default backend is in-process memory so local development and unit tests
    work without external services. PostgreSQL can be enabled by environment
    variables once database credentials are available.
    """

    backend = (
        str(getattr(settings, "memory_store_backend", "memory") or "memory")
        .strip()
        .lower()
    )

    if backend in {"memory", "inmemory", "in_memory"}:
        return InMemoryMemoryStore()

    if backend in {"postgres", "postgresql", "pg"}:
        database_url = str(getattr(settings, "memory_database_url", "") or "").strip()
        if not database_url:
            raise RuntimeError(
                "MEMORY_DATABASE_URL is required when MEMORY_STORE_BACKEND=postgres"
            )

        table_name = str(
            getattr(settings, "memory_postgres_table", "agent_memories")
            or "agent_memories"
        ).strip()
        auto_create = _truthy(getattr(settings, "memory_postgres_auto_create", False))
        return PostgresMemoryStore(
            database_url=database_url,
            table_name=table_name,
            auto_create=auto_create,
        )

    raise RuntimeError(
        f"Unsupported MEMORY_STORE_BACKEND={backend!r}. "
        "Supported values are: memory, postgres."
    )
