"""Tool execution with bounded transient retries and structured failures."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.messages import ToolMessage


@dataclass(frozen=True)
class ClassifiedToolError:
    error_type: str
    retryable: bool
    permission_error: bool = False


def classify_tool_error(exc: Exception) -> ClassifiedToolError:
    """Classify without depending on provider-specific exception packages."""

    text = f"{exc.__class__.__name__}: {exc}".lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    response = getattr(exc, "response", None)
    status = status or getattr(response, "status_code", None)
    if isinstance(exc, PermissionError) or status in {401, 403} or any(
        token in text
        for token in (
            "permission denied",
            "access denied",
            "forbidden",
            "unauthorized",
            "guardrail rejected",
            "policy rejected",
            "insufficientprivilege",
        )
    ):
        return ClassifiedToolError("permission_error", False, True)
    if status == 429 or "429" in text or "rate limit" in text:
        return ClassifiedToolError("rate_limit", True)
    if status in {502, 503} or any(
        token in text for token in ("502", "503", "bad gateway", "service unavailable")
    ):
        return ClassifiedToolError("service_unavailable", True)
    if any(token in text for token in ("timeout", "timed out", "deadline exceeded")):
        return ClassifiedToolError("timeout", True)
    if any(
        token in text
        for token in (
            "connection reset",
            "connection aborted",
            "connectionerror",
            "connectionreseterror",
            "broken pipe",
        )
    ):
        return ClassifiedToolError("connection_error", True)
    if any(
        token in text
        for token in (
            "validationerror",
            "invalid argument",
            "missing required",
            "argument error",
        )
    ):
        return ClassifiedToolError("argument_error", False)
    if any(
        token in text
        for token in (
            "schema",
            "column",
            "unknown field",
            "sql",
            "syntax error",
            "relation does not exist",
        )
    ):
        return ClassifiedToolError("schema_or_query_error", False)
    if any(
        token in text
        for token in (
            "business rule",
            "precondition",
            "condition not met",
            "not eligible",
            "insufficient",
        )
    ):
        return ClassifiedToolError("business_condition_error", False)
    return ClassifiedToolError("tool_error", False)


def parse_tool_error(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, ToolMessage) and getattr(message, "type", None) != "tool":
        return None
    content = getattr(message, "content", "")
    try:
        payload = json.loads(content) if isinstance(content, str) else content
    except (TypeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("status") == "error" else None


def _returned_error(value: Any) -> dict[str, Any] | None:
    payload = value
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) and payload.get("status") == "error" else None


def _classify_returned_error(payload: dict[str, Any]) -> ClassifiedToolError:
    error_type = str(payload.get("error_type") or "").strip().lower()
    permission_error = bool(payload.get("permission_error")) or error_type in {
        "permission_error",
        "permission_denied",
        "guardrail_rejected",
    }
    if permission_error:
        return ClassifiedToolError("permission_error", False, True)
    if error_type in {
        "timeout",
        "rate_limit",
        "service_unavailable",
        "connection_error",
    }:
        return ClassifiedToolError(error_type, True)
    classified = classify_tool_error(RuntimeError(str(payload.get("message") or payload)))
    if classified.error_type != "tool_error":
        return classified
    return ClassifiedToolError(error_type or "tool_error", bool(payload.get("retryable")))


class RecoveringToolNode:
    """A ToolNode-compatible synchronous node that retries only transient failures."""

    def __init__(
        self,
        tools: list[Any],
        *,
        max_retries: int,
        backoff_base_sec: float = 0.25,
        sleeper: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        self.tools = {
            str(getattr(tool, "name", tool.__class__.__name__)): tool for tool in tools
        }
        self.max_retries = max(0, max_retries)
        self.backoff_base_sec = max(0.0, backoff_base_sec)
        self.sleeper = sleeper
        self.random_fn = random_fn

    def invoke(
        self, state: dict[str, Any], *, remaining_budget: int | None = None,
        step_id: str | None = None, fallback_tools: set[str] | None = None,
    ) -> dict[str, Any]:
        messages = list(state.get("messages", []))
        calls = list(getattr(messages[-1], "tool_calls", []) or []) if messages else []
        results: list[ToolMessage] = []
        total_attempts = 0
        tool_events: list[dict[str, Any]] = []
        fallback_tools = fallback_tools or set()
        for index, call in enumerate(calls):
            name = str(
                call.get("name")
                if isinstance(call, dict)
                else getattr(call, "name", "")
            )
            args = (
                call.get("args", {})
                if isinstance(call, dict)
                else getattr(call, "args", {})
            )
            raw_call_id = (
                call.get("id")
                if isinstance(call, dict)
                else getattr(call, "id", None)
            )
            call_id = str(raw_call_id or f"call-{index}")
            tool = self.tools.get(name)
            attempts = 0
            while True:
                if remaining_budget is not None and total_attempts >= remaining_budget:
                    payload = self._error_payload(
                        name,
                        "budget_exhausted",
                        False,
                        "全局工具调用预算已达到上限",
                        attempts,
                    )
                    results.append(
                        ToolMessage(
                            content=json.dumps(payload, ensure_ascii=False),
                            tool_call_id=call_id,
                            name=name or "unknown_tool",
                        )
                    )
                    break
                attempts += 1
                total_attempts += 1
                started = time.perf_counter()
                try:
                    if tool is None:
                        raise ValueError(f"unknown tool: {name}")
                    value = tool.invoke(args)
                    returned_error = _returned_error(value)
                    if returned_error is not None:
                        classified = _classify_returned_error(returned_error)
                        tool_events.append(self._tool_event(
                            state, name, call_id, step_id, attempts, started,
                            "error", classified.error_type, classified.retryable,
                            False, name if name in fallback_tools else None,
                        ))
                        if classified.retryable and attempts <= self.max_retries:
                            delay = self.backoff_base_sec * (2 ** (attempts - 1))
                            self.sleeper(delay * (0.5 + self.random_fn()))
                            continue
                        payload = self._error_payload(
                            name,
                            classified.error_type,
                            classified.retryable,
                            str(returned_error.get("message") or "工具返回错误状态"),
                            attempts,
                            classified.permission_error,
                        )
                        results.append(
                            ToolMessage(
                                content=json.dumps(payload, ensure_ascii=False),
                                tool_call_id=call_id,
                                name=name,
                            )
                        )
                        break
                    content = (
                        value
                        if isinstance(value, str)
                        else json.dumps(value, ensure_ascii=False, default=str)
                    )
                    results.append(ToolMessage(content=content, tool_call_id=call_id, name=name))
                    tool_events.append(self._tool_event(
                        state, name, call_id, step_id, attempts, started, "success",
                        None, False, attempts > 1, name if name in fallback_tools else None,
                    ))
                    break
                except Exception as exc:  # tools intentionally expose heterogeneous errors
                    classified = classify_tool_error(exc)
                    tool_events.append(self._tool_event(
                        state, name, call_id, step_id, attempts, started, "error",
                        classified.error_type, classified.retryable, False,
                        name if name in fallback_tools else None,
                    ))
                    if classified.retryable and attempts <= self.max_retries:
                        delay = self.backoff_base_sec * (2 ** (attempts - 1))
                        self.sleeper(delay * (0.5 + self.random_fn()))
                        continue
                    payload = self._error_payload(
                        name,
                        classified.error_type,
                        classified.retryable,
                        str(exc),
                        attempts,
                        classified.permission_error,
                    )
                    results.append(
                        ToolMessage(
                            content=json.dumps(payload, ensure_ascii=False),
                            tool_call_id=call_id,
                            name=name or "unknown_tool",
                        )
                    )
                    break
        return {"messages": results, "attempts": total_attempts, "tool_events": tool_events}

    @staticmethod
    def _tool_event(
        state: dict[str, Any], tool_name: str, tool_call_id: str,
        step_id: str | None, attempt: int, started: float, status: str,
        error_type: str | None, retryable: bool, recovered: bool,
        fallback_tool: str | None,
    ) -> dict[str, Any]:
        return {
            "trace_id": state.get("trace_id"),
            "thread_id": state.get("trace_thread_id"),
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "step_id": step_id,
            "attempt": attempt,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "status": status,
            "error_type": error_type,
            "retryable": retryable,
            "recovered": recovered,
            "fallback_tool": fallback_tool,
        }

    @staticmethod
    def _error_payload(
        tool: str,
        error_type: str,
        retryable: bool,
        message: str,
        attempts: int,
        permission_error: bool = False,
    ) -> dict[str, Any]:
        return {
            "status": "error",
            "tool": tool,
            "error_type": error_type,
            "retryable": retryable,
            "permission_error": permission_error,
            "message": message,
            "attempts": attempts,
        }
