"""Memory HTTP routes for manual memory management and thread summarization."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from chaincloud_agent_service.api.auth import require_authenticated_user
from chaincloud_agent_service.auth.store import UserRecord
from chaincloud_agent_service.memory.models import MemoryRecord
from chaincloud_agent_service.memory.service import (
    DEFAULT_MAX_MESSAGES,
    DEFAULT_TRANSCRIPT_MAX_CHARS,
    MemoryService,
)

router = APIRouter(prefix="/memory")


class SaveMemoryRequest(BaseModel):
    memory_key: str = Field(..., min_length=1, description="记忆唯一键")
    summary: str = Field(..., min_length=1, description="长期记忆摘要")
    source_thread_id: str = Field(..., min_length=1, description="来源会话 thread_id")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据")


class SummarizeMemoryRequest(BaseModel):
    thread_id: str = Field(..., min_length=1, description="需要总结的会话 thread_id")
    memory_key: str = Field(..., min_length=1, description="保存后的 memory key")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据")
    max_messages: int = Field(default=DEFAULT_MAX_MESSAGES, ge=1, le=200)
    max_chars: int = Field(default=DEFAULT_TRANSCRIPT_MAX_CHARS, ge=500, le=50000)


class MemoryListResponse(BaseModel):
    memories: list[MemoryRecord]


def _memory_service(request: Request) -> MemoryService:
    service = getattr(request.app.state, "memory_service", None)
    if service is None:
        raise HTTPException(status_code=500, detail="memory service is not initialized")
    return service


def _memory_llm(request: Request) -> Any:
    llm = getattr(request.app.state, "memory_llm", None)
    if llm is None:
        raise HTTPException(status_code=500, detail="memory llm is not initialized")
    return llm


def _graph(request: Request) -> Any:
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=500, detail="graph is not initialized")
    return graph


def _metadata_matches_user(metadata: dict[str, Any], user: UserRecord) -> bool:
    metadata_user_id = metadata.get("user_id")
    if isinstance(metadata_user_id, str) and metadata_user_id == user.user_id:
        return True
    metadata_username = metadata.get("username")
    if isinstance(metadata_username, str) and metadata_username == user.username:
        return True
    return False


def _memory_key_matches_legacy_user_prefix(memory_key: str, user: UserRecord) -> bool:
    safe_username = user.username.strip().lower()
    return bool(safe_username) and memory_key.startswith(f"{safe_username}-")


def _memory_belongs_to_user(record: MemoryRecord, user: UserRecord) -> bool:
    return _metadata_matches_user(
        record.metadata, user
    ) or _memory_key_matches_legacy_user_prefix(record.memory_key, user)


def _require_owned_memory(
    record: MemoryRecord | None, user: UserRecord
) -> MemoryRecord:
    if record is None or not _memory_belongs_to_user(record, user):
        raise HTTPException(status_code=404, detail="memory not found")
    return record


def _attach_user_metadata(metadata: dict[str, Any], user: UserRecord) -> dict[str, Any]:
    merged = dict(metadata)
    merged.update({"user_id": user.user_id, "username": user.username})
    return merged


async def _read_thread_messages(graph: Any, thread_id: str) -> list[Any]:
    config = {"configurable": {"thread_id": thread_id}}
    if hasattr(graph, "aget_state"):
        state = await graph.aget_state(config)
    elif hasattr(graph, "get_state"):
        state = graph.get_state(config)
    else:
        raise HTTPException(
            status_code=500, detail="graph does not support state reading"
        )

    values = getattr(state, "values", None)
    if values is None and isinstance(state, dict):
        values = state
    if not isinstance(values, dict):
        raise HTTPException(status_code=404, detail="thread state not found")
    messages = values.get("messages", [])
    if not messages:
        raise HTTPException(status_code=404, detail="thread messages not found")
    return list(messages)


@router.post("", response_model=MemoryRecord)
async def save_memory(
    request: Request,
    body: SaveMemoryRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> MemoryRecord:
    current_user = require_authenticated_user(request, authorization)
    service = _memory_service(request)
    return service.save_memory(
        memory_key=body.memory_key,
        summary=body.summary,
        source_thread_id=body.source_thread_id,
        metadata=_attach_user_metadata(body.metadata, current_user),
    )


@router.post("/summarize", response_model=MemoryRecord)
async def summarize_memory(
    request: Request,
    body: SummarizeMemoryRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> MemoryRecord:
    current_user = require_authenticated_user(request, authorization)
    service = _memory_service(request)
    llm = _memory_llm(request)
    graph = _graph(request)
    messages = await _read_thread_messages(graph, body.thread_id)
    metadata = _attach_user_metadata(
        {**body.metadata, "summary_source": "thread_checkpoint"}, current_user
    )
    return await service.summarize_and_save(
        llm=llm,
        messages=messages,
        memory_key=body.memory_key,
        source_thread_id=body.thread_id,
        metadata=metadata,
        max_messages=body.max_messages,
        max_chars=body.max_chars,
    )


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> MemoryListResponse:
    current_user = require_authenticated_user(request, authorization)
    service = _memory_service(request)
    memories = [
        record
        for record in service.list_memories()
        if _memory_belongs_to_user(record, current_user)
    ]
    return MemoryListResponse(memories=memories)


@router.get("/{memory_key}", response_model=MemoryRecord)
async def get_memory(
    memory_key: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> MemoryRecord:
    current_user = require_authenticated_user(request, authorization)
    service = _memory_service(request)
    return _require_owned_memory(service.get_memory(memory_key), current_user)


@router.delete("/{memory_key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_key: str,
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    current_user = require_authenticated_user(request, authorization)
    service = _memory_service(request)
    record = _require_owned_memory(service.get_memory(memory_key), current_user)
    deleted = service.delete_memory(record.memory_key)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory not found")
    return None
