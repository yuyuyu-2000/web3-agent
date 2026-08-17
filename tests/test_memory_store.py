from __future__ import annotations

from datetime import datetime, timezone

import pytest

from chaincloud_agent_service.memory.models import MemoryRecord
from chaincloud_agent_service.memory.store import InMemoryMemoryStore, PostgresMemoryStore


def test_memory_record_requires_non_empty_key_and_thread_id() -> None:
    with pytest.raises(ValueError):
        MemoryRecord(memory_key="", summary="summary", source_thread_id="thread-1")

    with pytest.raises(ValueError):
        MemoryRecord(memory_key="user-a", summary="summary", source_thread_id="")


def test_save_and_get_memory_record() -> None:
    store = InMemoryMemoryStore()

    record = store.save(
        memory_key="user-a",
        summary="用户偏好使用中文回答。",
        source_thread_id="thread-1",
        metadata={"source": "unit-test"},
    )

    assert record.memory_key == "user-a"
    assert record.summary == "用户偏好使用中文回答。"
    assert record.source_thread_id == "thread-1"
    assert record.metadata == {"source": "unit-test"}
    assert store.get("user-a") == record


def test_save_overwrites_existing_memory_key() -> None:
    store = InMemoryMemoryStore()

    store.save(
        memory_key="user-a",
        summary="旧摘要",
        source_thread_id="thread-1",
    )
    updated = store.save(
        memory_key="user-a",
        summary="新摘要",
        source_thread_id="thread-2",
    )

    assert store.get("user-a") == updated
    assert store.get("user-a").summary == "新摘要"  # type: ignore[union-attr]
    assert len(store.list()) == 1


def test_list_returns_records_by_updated_at_descending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter(
        [
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 3, tzinfo=timezone.utc),
            datetime(2026, 1, 2, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(
        "chaincloud_agent_service.memory.store.utc_now",
        lambda: next(times),
    )
    store = InMemoryMemoryStore()

    store.save(memory_key="first", summary="第一条", source_thread_id="thread-1")
    store.save(memory_key="latest", summary="最新一条", source_thread_id="thread-2")
    store.save(memory_key="middle", summary="中间一条", source_thread_id="thread-3")

    assert [record.memory_key for record in store.list()] == [
        "latest",
        "middle",
        "first",
    ]


def test_delete_existing_and_missing_memory_record() -> None:
    store = InMemoryMemoryStore()
    store.save(memory_key="user-a", summary="摘要", source_thread_id="thread-1")

    assert store.delete("user-a") is True
    assert store.get("user-a") is None
    assert store.delete("user-a") is False


def test_clear_removes_all_memory_records() -> None:
    store = InMemoryMemoryStore()
    store.save(memory_key="user-a", summary="摘要 A", source_thread_id="thread-1")
    store.save(memory_key="user-b", summary="摘要 B", source_thread_id="thread-2")

    store.clear()

    assert store.list() == []
    assert store.get("user-a") is None
    assert store.get("user-b") is None


def test_postgres_old_row_without_recall_columns_is_compatible() -> None:
    store = PostgresMemoryStore(database_url="postgresql://unused", auto_create=False)
    updated = datetime(2026, 1, 1, tzinfo=timezone.utc)
    record = store._row_to_record({  # noqa: SLF001 - compatibility unit test
        "memory_key": "legacy",
        "summary": "旧摘要",
        "source_thread_id": "thread-old",
        "metadata": {"user_id": "alice"},
        "updated_at": updated,
    })
    assert record.memory_key == "legacy"
    assert record.embedding is None
    assert record.created_at == updated
