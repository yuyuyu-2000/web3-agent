from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryRecord(BaseModel):
    memory_key: str = Field(..., min_length=1, description="记忆唯一键")
    summary: str = Field(..., description="长期记忆摘要")
    source_thread_id: str = Field(..., min_length=1, description="来源会话 thread_id")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据")
    updated_at: datetime = Field(default_factory=utc_now, description="最后更新时间")
