"""
会话 checkpoint：供 graph.compile(checkpointer=...) 使用。

- **PostgreSQL**：`AsyncPostgresSaver`（`langgraph-checkpoint-postgres`），持久化、可跨进程。
- **内存**：`MemorySaver`（LangGraph 内置），按 thread_id 保留上下文，仅当前进程内有效，重启即清空。

上线或需要持久化时配置 `DATABASE_URL` 即可切到 Postgres，无需改 graph 代码。
"""

from __future__ import annotations

import pickle
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class _CheckpointSerde(JsonPlusSerializer):
    """LangGraph 默认 JsonPlusSerializer 仅在 MsgpackEncodeError 时 pickle 回退；

    ormsgpack 对若干嵌套类型会直接抛 ``TypeError: ... not msgpack serializable``，
    导致 MemorySaver.put_writes 失败；此处一并回退到 pickle（与内存/PG checkpoint 常见用法一致）。
    """

    def __init__(self) -> None:
        super().__init__(pickle_fallback=True)

    def dumps_typed(self, obj: object) -> tuple[str, bytes]:  # type: ignore[override]
        try:
            return super().dumps_typed(obj)
        except TypeError as exc:
            if "not msgpack serializable" in str(exc):
                return "pickle", pickle.dumps(obj)
            raise


_SERDE = _CheckpointSerde()


def memory_checkpointer() -> MemorySaver:
    """进程内 checkpoint；不与数据库交互。"""
    return MemorySaver(serde=_SERDE)


@asynccontextmanager
async def postgres_checkpointer(
    database_url: str,
) -> AsyncIterator[AsyncPostgresSaver]:
    async with AsyncPostgresSaver.from_conn_string(database_url, serde=_SERDE) as saver:
        await saver.setup()
        yield saver
