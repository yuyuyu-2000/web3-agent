from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

EXECUTION_EVENT_KEYS = (
    "node_events",
    "tool_events",
    "decision_events",
    "error_events",
    "context_events",
    "compact_events",
    "memory_recall_events",
)


class ChatTraceEvent(BaseModel):
    step: int
    message_index: int
    type: str
    tool: str | None = None
    tool_call_id: str | None = None
    status: str | None = None
    args_preview: str | None = None
    content_preview: str | None = None
    error_preview: str | None = None


_SENSITIVE_KEYWORDS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "token",
    "password",
    "passwd",
    "secret",
    "private_key",
    "privatekey",
)

_NON_SENSITIVE_USAGE_KEYS = {
    "tokens",
    "category_tokens",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "max_input_tokens",
    "remaining_input_tokens",
    "reserved_output_tokens",
    "rolling_summary_max_input_tokens",
    "memory_recall_context_tokens",
}


def _is_sensitive_key(key: Any) -> bool:
    key_text = str(key).lower()
    if key_text in _NON_SENSITIVE_USAGE_KEYS:
        return False
    return any(keyword in key_text for keyword in _SENSITIVE_KEYWORDS)


def _redact_for_trace(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_for_trace(item)
        return redacted

    if isinstance(value, list):
        return [_redact_for_trace(item) for item in value]

    if isinstance(value, tuple):
        return tuple(_redact_for_trace(item) for item in value)

    return value


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_execution_context(thread_id: str) -> dict[str, Any]:
    """Create the per-request fields stored in AgentState/checkpoints."""
    return {
        "trace_id": uuid.uuid4().hex,
        "trace_thread_id": thread_id,
        "trace_started_at": utc_now_iso(),
        "trace_started_monotonic": time.monotonic(),
        "node_events": [],
        "tool_events": [],
        "decision_events": [],
        "error_events": [],
        "context_events": [],
        "compact_events": [],
        "request_summary": None,
    }


def safe_trace_event(event: dict[str, Any]) -> dict[str, Any]:
    """Redact trace payloads; observability failures must never affect execution."""
    try:
        return _redact_for_trace(event)
    except Exception:  # pragma: no cover - defensive isolation
        logger.exception("failed to sanitize agent trace event")
        return {"type": "trace_sanitization_failed"}


def append_trace_event(
    state: dict[str, Any], update: dict[str, Any], bucket: str, event: dict[str, Any]
) -> None:
    try:
        update[bucket] = [*state.get(bucket, []), safe_trace_event(event)]
    except Exception:  # pragma: no cover - observability is best effort
        logger.exception("failed to append agent trace event")


def traced_node(node_name: str, function: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a sync LangGraph node and add one best-effort node event."""

    def wrapped(state: dict[str, Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        start_time = utc_now_iso()
        try:
            result = function(state, *args, **kwargs)
        except Exception as exc:
            # State updates cannot be checkpointed when LangGraph re-raises. Emit a
            # redacted structured log so the failure location is still observable.
            logger.exception(
                "agent node failed trace_id=%s thread_id=%s node=%s error_type=%s",
                state.get("trace_id"),
                state.get("trace_thread_id"),
                node_name,
                exc.__class__.__name__,
            )
            raise
        update = dict(result or {})
        append_trace_event(
            state,
            update,
            "node_events",
            {
                "trace_id": state.get("trace_id"),
                "thread_id": state.get("trace_thread_id"),
                "node_name": node_name,
                "start_time": start_time,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "status": "success",
            },
        )
        return update

    return wrapped


def traced_async_node(
    node_name: str, function: Callable[..., Awaitable[Any]]
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Async counterpart of :func:`traced_node`."""

    async def wrapped(
        state: dict[str, Any], *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        started = time.perf_counter()
        start_time = utc_now_iso()
        try:
            result = await function(state, *args, **kwargs)
        except Exception as exc:
            logger.exception(
                "agent node failed trace_id=%s thread_id=%s node=%s error_type=%s",
                state.get("trace_id"),
                state.get("trace_thread_id"),
                node_name,
                exc.__class__.__name__,
            )
            raise
        update = dict(result or {})
        append_trace_event(
            state,
            update,
            "node_events",
            {
                "trace_id": state.get("trace_id"),
                "thread_id": state.get("trace_thread_id"),
                "node_name": node_name,
                "start_time": start_time,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "status": "success",
            },
        )
        return update

    return wrapped


def build_request_summary(state: dict[str, Any]) -> dict[str, Any]:
    node_events = list(state.get("node_events", []))
    tool_events = list(state.get("tool_events", []))
    decision_events = list(state.get("decision_events", []))
    error_events = list(state.get("error_events", []))
    context_events = list(state.get("context_events", []))
    compact_events = list(state.get("compact_events", []))
    started = state.get("trace_started_monotonic")
    duration_ms = (
        round((time.monotonic() - float(started)) * 1000, 3)
        if isinstance(started, (int, float))
        else sum(float(e.get("duration_ms", 0)) for e in node_events)
    )
    status = state.get("status")
    if status == "completed":
        final_status = "success"
    elif status in {
        "partial",
        "degraded",
        "waiting_confirmation",
        "blocked_missing_state",
    }:
        final_status = "partial" if status == "partial" else "degraded"
    else:
        final_status = "failed"
    llm_nodes = {"executor", "direct_agent", "evaluator", "compose_answer", "reviewer"}
    router_model_calls = sum(
        1
        for event in decision_events
        if event.get("decision_type") == "router" and event.get("source") == "model"
    )
    rolling_summary_calls = sum(
        1 for event in compact_events if event.get("status") in {"success", "failed"}
    )
    input_tokens = output_tokens = 0
    token_data_available = False
    for message in state.get("messages", []):
        usage = getattr(message, "usage_metadata", None) or {}
        if usage:
            token_data_available = True
            input_tokens += int(usage.get("input_tokens", 0) or 0)
            output_tokens += int(usage.get("output_tokens", 0) or 0)
    return safe_trace_event(
        {
            "trace_id": state.get("trace_id"),
            "thread_id": state.get("trace_thread_id"),
            "total_duration_ms": duration_ms,
            "llm_calls": (
                sum(1 for event in node_events if event.get("node_name") in llm_nodes)
                + int(state.get("planner_attempts", 0))
                + router_model_calls
                + rolling_summary_calls
            ),
            "tool_calls": sum(1 for event in tool_events if event.get("attempt") == 1),
            "tool_retries": sum(
                1 for event in tool_events if int(event.get("attempt", 1)) > 1
            ),
            "step_retries": sum(
                1
                for event in decision_events
                if event.get("decision_type") == "evaluator"
                and event.get("action") == "retry"
            ),
            "permission_checks": sum(
                1
                for event in decision_events
                if event.get("decision_type") == "permission_gate"
            ),
            "fallbacks": sum(
                1
                for event in tool_events
                if event.get("fallback_tool") and event.get("status") == "success"
            ),
            "errors": len(error_events),
            "context_builds": len(context_events),
            "context_trimmed_items": sum(
                len(event.get("trimmed", [])) for event in context_events
            ),
            "rolling_compacts": sum(
                1 for event in compact_events if event.get("status") == "success"
            ),
            "compact_failures": sum(
                1 for event in compact_events if event.get("status") == "failed"
            ),
            "rolling_summary_calls": rolling_summary_calls,
            "replans": int(state.get("replanning_count", 0) or 0),
            "input_tokens": input_tokens if token_data_available else None,
            "output_tokens": output_tokens if token_data_available else None,
            "total_tokens": (input_tokens + output_tokens)
            if token_data_available
            else None,
            "final_status": final_status,
        }
    )


def execution_trace_from_state(state: dict[str, Any]) -> dict[str, Any]:
    return safe_trace_event(
        {
            "trace_id": state.get("trace_id"),
            "thread_id": state.get("trace_thread_id"),
            **{key: list(state.get(key, [])) for key in EXECUTION_EVENT_KEYS},
            "tool_result_records": list(state.get("tool_result_records", [])),
            "rolling_summary": {
                "summary_version": state.get("summary_version", 0),
                "summarized_until": state.get("summarized_until", 0),
                "summary_updated_at": state.get("summary_updated_at"),
                "compact_failure_count": state.get("compact_failure_count", 0),
            },
            "request_summary": state.get("request_summary")
            or build_request_summary(state),
        }
    )


def _preview(value: Any, max_len: int = 500) -> str:
    value = _redact_for_trace(value)

    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = str(value)

    if len(text) <= max_len:
        return text
    return text[:max_len] + "...[truncated]"


def _get_tool_call_field(call: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(call, dict):
        if field_name in call:
            return call.get(field_name, default)

        function = call.get("function")
        if isinstance(function, dict):
            if field_name == "name":
                return function.get("name", default)
            if field_name in {"args", "arguments"}:
                return function.get("arguments", default)

        return default

    return getattr(call, field_name, default)


def _extract_tool_calls_from_message(msg: Any) -> list[Any]:
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        return list(tool_calls)

    additional_kwargs = getattr(msg, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        raw_tool_calls = additional_kwargs.get("tool_calls")
        if raw_tool_calls:
            return list(raw_tool_calls)

    return []


def _extract_invalid_tool_calls_from_message(msg: Any) -> list[Any]:
    invalid_tool_calls = getattr(msg, "invalid_tool_calls", None)
    if invalid_tool_calls:
        return list(invalid_tool_calls)
    return []


def extract_agent_trace(
    messages: list[Any], max_preview_chars: int = 500
) -> list[ChatTraceEvent]:
    trace: list[ChatTraceEvent] = []

    for msg_index, msg in enumerate(messages):
        msg_type = getattr(msg, "type", None)
        msg_class = msg.__class__.__name__

        for call in _extract_tool_calls_from_message(msg):
            tool_name = _get_tool_call_field(call, "name")
            tool_args = _get_tool_call_field(call, "args")
            if tool_args is None:
                tool_args = _get_tool_call_field(call, "arguments")
            tool_call_id = _get_tool_call_field(call, "id")

            trace.append(
                ChatTraceEvent(
                    step=len(trace) + 1,
                    message_index=msg_index,
                    type="tool_call_request",
                    tool=str(tool_name) if tool_name is not None else None,
                    tool_call_id=str(tool_call_id)
                    if tool_call_id is not None
                    else None,
                    status="requested",
                    args_preview=_preview(tool_args, max_preview_chars),
                )
            )

        for invalid_call in _extract_invalid_tool_calls_from_message(msg):
            tool_name = _get_tool_call_field(invalid_call, "name")
            tool_args = _get_tool_call_field(invalid_call, "args")
            tool_error = _get_tool_call_field(invalid_call, "error")
            tool_call_id = _get_tool_call_field(invalid_call, "id")

            trace.append(
                ChatTraceEvent(
                    step=len(trace) + 1,
                    message_index=msg_index,
                    type="invalid_tool_call",
                    tool=str(tool_name) if tool_name is not None else None,
                    tool_call_id=str(tool_call_id)
                    if tool_call_id is not None
                    else None,
                    status="invalid",
                    args_preview=_preview(tool_args, max_preview_chars),
                    error_preview=_preview(tool_error, max_preview_chars),
                )
            )

        if msg_type == "tool" or msg_class == "ToolMessage":
            tool_name = getattr(msg, "name", None)
            tool_call_id = getattr(msg, "tool_call_id", None)
            status = getattr(msg, "status", None) or "completed"
            content = getattr(msg, "content", "")

            trace.append(
                ChatTraceEvent(
                    step=len(trace) + 1,
                    message_index=msg_index,
                    type="tool_result",
                    tool=str(tool_name) if tool_name is not None else None,
                    tool_call_id=str(tool_call_id)
                    if tool_call_id is not None
                    else None,
                    status=str(status),
                    content_preview=_preview(content, max_preview_chars),
                )
            )

    return trace
