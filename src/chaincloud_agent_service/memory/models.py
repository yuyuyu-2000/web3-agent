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
    user_id: str | None = Field(default=None, description="所属用户；旧记录允许为空")
    memory_type: str | None = Field(default=None, description="记忆类型")
    embedding: list[float] | None = Field(default=None, exclude=True)
    created_at: datetime = Field(default_factory=utc_now, description="创建时间")
    updated_at: datetime = Field(default_factory=utc_now, description="最后更新时间")


class MemoryCandidate(BaseModel):
    record: MemoryRecord
    similarity: float
    final_score: float
