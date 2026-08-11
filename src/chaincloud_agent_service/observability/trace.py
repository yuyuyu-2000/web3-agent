from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


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


def _is_sensitive_key(key: Any) -> bool:
    key_text = str(key).lower()
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
