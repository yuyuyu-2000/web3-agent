from __future__ import annotations

import json

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from chaincloud_agent_service.agent.tool_recovery import (
    RecoveringToolNode,
    classify_tool_error,
    parse_tool_error,
)


def _state(name: str = "lookup") -> dict:
    return {
        "messages": [
            AIMessage(content="", tool_calls=[{"name": name, "args": {"value": 1}, "id": "call-1", "type": "tool_call"}])
        ]
    }


def test_transient_error_retries_inside_tool_node_then_succeeds() -> None:
    calls = 0

    def lookup(value: int) -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("request timed out")
        return f"ok:{value}"

    tool = StructuredTool.from_function(lookup, name="lookup", description="lookup")
    node = RecoveringToolNode(tool and [tool], max_retries=2, sleeper=lambda _: None, random_fn=lambda: 0.5)
    result = node.invoke(_state())

    assert result["attempts"] == 3
    assert result["messages"][0].content == "ok:1"


def test_non_retryable_error_returns_structured_message_once() -> None:
    calls = 0

    def lookup(value: int) -> str:
        nonlocal calls
        calls += 1
        raise ValueError("invalid argument: value")

    tool = StructuredTool.from_function(lookup, name="lookup", description="lookup")
    result = RecoveringToolNode([tool], max_retries=5, sleeper=lambda _: None).invoke(_state())
    payload = parse_tool_error(result["messages"][0])

    assert calls == 1
    assert result["attempts"] == 1
    assert payload == {
        "status": "error", "tool": "lookup", "error_type": "argument_error",
        "retryable": False, "permission_error": False,
        "message": "invalid argument: value", "attempts": 1,
    }


def test_permission_error_is_never_retryable() -> None:
    classified = classify_tool_error(PermissionError("permission denied by guardrail"))
    assert classified.permission_error is True
    assert classified.retryable is False


def test_retry_respects_remaining_global_budget() -> None:
    def lookup(value: int) -> str:
        raise ConnectionResetError("connection reset by peer")

    tool = StructuredTool.from_function(lookup, name="lookup", description="lookup")
    result = RecoveringToolNode([tool], max_retries=5, sleeper=lambda _: None).invoke(_state(), remaining_budget=1)
    payload = json.loads(result["messages"][0].content)
    assert result["attempts"] == 1
    assert payload["error_type"] == "budget_exhausted"


def test_error_return_value_is_treated_as_failure() -> None:
    def lookup(value: int) -> dict:
        return {"status": "error", "error_type": "schema_error", "message": "字段不存在"}

    tool = StructuredTool.from_function(lookup, name="lookup", description="lookup")
    result = RecoveringToolNode([tool], max_retries=3, sleeper=lambda _: None).invoke(_state())
    payload = parse_tool_error(result["messages"][0])

    assert result["attempts"] == 1
    assert payload is not None
    assert payload["error_type"] == "schema_error"
    assert payload["retryable"] is False


def test_transient_error_return_value_is_retried() -> None:
    calls = 0

    def lookup(value: int) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"status": "error", "error_type": "timeout", "message": "upstream timeout"}
        return {"status": "success", "value": value}

    tool = StructuredTool.from_function(lookup, name="lookup", description="lookup")
    result = RecoveringToolNode([tool], max_retries=1, sleeper=lambda _: None).invoke(_state())

    assert result["attempts"] == 2
    assert json.loads(result["messages"][0].content)["status"] == "success"
