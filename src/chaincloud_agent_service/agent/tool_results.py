"""Three-layer tool results: raw persistence, structured facts, and LLM summaries."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from langchain_core.messages import ToolMessage

from chaincloud_agent_service.agent.answer_composer.evidence import (
    classify_tool_evidence_level,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RawToolResult:
    result_id: str
    tool_name: str
    tool_args: dict[str, Any]
    created_at: str
    evidence_source: str
    location: str
    content_sha256: str
    size_bytes: int


class ToolResultStore(Protocol):
    def save(self, *, tool_name: str, tool_args: dict[str, Any], raw_content: str,
             evidence_source: str) -> RawToolResult: ...
    def read(self, result_id: str) -> str: ...


class FileToolResultStore:
    """Audit-oriented filesystem store; each raw result is an immutable JSON file."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def save(self, *, tool_name: str, tool_args: dict[str, Any], raw_content: str,
             evidence_source: str) -> RawToolResult:
        self.directory.mkdir(parents=True, exist_ok=True)
        result_id = uuid.uuid4().hex
        created_at = utc_now_iso()
        path = self.directory / f"{result_id}.json"
        payload = {
            "result_id": result_id, "tool_name": tool_name, "tool_args": tool_args,
            "created_at": created_at, "evidence_source": evidence_source,
            "raw_content": raw_content,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        return RawToolResult(
            result_id=result_id, tool_name=tool_name, tool_args=tool_args,
            created_at=created_at, evidence_source=evidence_source,
            location=str(path.resolve()),
            content_sha256=hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
            size_bytes=len(raw_content.encode("utf-8")),
        )

    def read(self, result_id: str) -> str:
        if not result_id or any(char not in "0123456789abcdef" for char in result_id):
            raise ValueError("invalid result_id")
        path = self.directory / f"{result_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("result_id") != result_id:
            raise ValueError("tool result id mismatch")
        return str(payload["raw_content"])


class InMemoryToolResultStore:
    """Test/local alternative with the same traceability contract."""

    def __init__(self) -> None:
        self._results: dict[str, tuple[RawToolResult, str]] = {}

    def save(self, *, tool_name: str, tool_args: dict[str, Any], raw_content: str,
             evidence_source: str) -> RawToolResult:
        result_id = uuid.uuid4().hex
        record = RawToolResult(
            result_id=result_id, tool_name=tool_name, tool_args=tool_args,
            created_at=utc_now_iso(), evidence_source=evidence_source,
            location=f"memory://tool-results/{result_id}",
            content_sha256=hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
            size_bytes=len(raw_content.encode("utf-8")),
        )
        self._results[result_id] = (record, raw_content)
        return record

    def read(self, result_id: str) -> str:
        return self._results[result_id][1]


def extract_structured_facts(raw_content: str, *, max_facts: int = 20) -> dict[str, Any]:
    """Deterministically retain high-signal shape, identifiers, and scalar facts."""
    try:
        value = json.loads(raw_content)
    except json.JSONDecodeError:
        return {"text_preview": raw_content[:1000], "char_count": len(raw_content)}

    facts: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if len(facts) >= max_facts:
                break
            if isinstance(item, (str, int, float, bool)) or item is None:
                facts[str(key)] = _bounded_value(item)
            elif isinstance(item, list):
                facts[f"{key}_count"] = len(item)
                if item and isinstance(item[0], dict):
                    facts[f"{key}_fields"] = list(item[0].keys())[:20]
                    facts[f"{key}_sample"] = _bounded_value(item[:2])
            elif isinstance(item, dict):
                facts[f"{key}_fields"] = list(item.keys())[:20]
                scalar = {k: v for k, v in item.items() if isinstance(v, (str, int, float, bool)) or v is None}
                if scalar:
                    facts[f"{key}_values"] = _bounded_value(dict(list(scalar.items())[:10]))
    elif isinstance(value, list):
        facts["row_count"] = len(value)
        if value:
            facts["sample"] = _bounded_value(value[:2])
            if isinstance(value[0], dict):
                facts["fields"] = list(value[0].keys())[:20]
    else:
        facts["value"] = _bounded_value(value)
    return facts


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return "...[nested truncated]"
    if isinstance(value, str):
        return value if len(value) <= 500 else value[:500] + "...[truncated]"
    if isinstance(value, list):
        return [_bounded_value(item, depth=depth + 1) for item in value[:5]]
    if isinstance(value, dict):
        return {
            str(key): _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    return value


def build_context_summary(*, tool_name: str, status: str, facts: dict[str, Any],
                          raw: RawToolResult, preview: str, compressed: bool,
                          error: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "status": status,
        "tool_name": tool_name,
        "summary": "工具执行失败" if error else "工具执行成功；完整结果已保存，可按 result_id 追溯。",
        "key_facts": facts,
        "result_id": raw.result_id,
        "reference": raw.location,
        "created_at": raw.created_at,
        "evidence_level": raw.evidence_source,
        "preview": preview,
        "compressed": compressed,
    }
    if error:
        payload["error"] = error
    return payload


def process_tool_result(*, store: ToolResultStore, tool_name: str,
                        tool_args: dict[str, Any], raw_content: str,
                        threshold_bytes: int, preview_chars: int,
                        error: dict[str, Any] | None = None) -> tuple[ToolMessage, dict[str, Any]]:
    evidence = classify_tool_evidence_level(tool_name).value
    raw = store.save(tool_name=tool_name, tool_args=tool_args, raw_content=raw_content,
                     evidence_source=evidence)
    facts = extract_structured_facts(raw_content)
    compressed = raw.size_bytes > threshold_bytes and error is None
    summary = build_context_summary(
        tool_name=tool_name, status="error" if error else "success", facts=facts,
        raw=raw, preview=raw_content[:preview_chars], compressed=compressed, error=error,
    )
    content = json.dumps(summary, ensure_ascii=False, default=str) if compressed else raw_content
    metadata = {
        "result_id": raw.result_id, "tool_name": tool_name, "tool_args": tool_args,
        "created_at": raw.created_at, "evidence_source": evidence,
        "raw_result_location": raw.location, "raw_size_bytes": raw.size_bytes,
        "content_sha256": raw.content_sha256, "structured_facts": facts,
        "context_summary": summary, "compressed": compressed,
    }
    message = ToolMessage(content=content, tool_call_id="pending", name=tool_name,
                          additional_kwargs={"tool_result": metadata})
    return message, metadata


def tool_result_metadata(message: Any) -> dict[str, Any] | None:
    kwargs = getattr(message, "additional_kwargs", None)
    value = kwargs.get("tool_result") if isinstance(kwargs, dict) else None
    return value if isinstance(value, dict) else None


def tool_message_for_context(message: ToolMessage, *, require_raw: bool = False,
                             compact_old: bool = False) -> ToolMessage:
    metadata = tool_result_metadata(message)
    if not metadata or (require_raw and not compact_old):
        return message
    if not metadata.get("compressed") and not compact_old:
        return message
    summary = dict(metadata.get("context_summary") or {})
    if compact_old:
        summary["summary"] = "较早工具结果已压缩；完整内容可通过 result_id 追溯。"
        summary["preview"] = str(summary.get("preview", ""))[:300]
    return ToolMessage(
        content=json.dumps(summary, ensure_ascii=False, default=str),
        tool_call_id=str(getattr(message, "tool_call_id", "")),
        name=str(getattr(message, "name", "unknown_tool")),
        additional_kwargs=getattr(message, "additional_kwargs", {}),
    )
