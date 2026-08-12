from chaincloud_agent_service.observability.trace import (
    build_request_summary,
    execution_trace_from_state,
    extract_agent_trace,
    new_execution_context,
)


class DummyAIMessage:
    def __init__(
        self, tool_calls=None, additional_kwargs=None, invalid_tool_calls=None
    ):
        self.tool_calls = tool_calls
        self.additional_kwargs = additional_kwargs or {}
        self.invalid_tool_calls = invalid_tool_calls or []
        self.type = "ai"


class DummyToolMessage:
    def __init__(self, name, content, tool_call_id="call_1", status="completed"):
        self.name = name
        self.content = content
        self.tool_call_id = tool_call_id
        self.status = status
        self.type = "tool"


def test_extract_agent_trace_with_tool_call_and_result():
    messages = [
        DummyAIMessage(
            tool_calls=[
                {
                    "id": "call_1",
                    "name": "ethereum_jsonrpc",
                    "args": {"method": "eth_getTransactionByHash"},
                }
            ]
        ),
        DummyToolMessage(
            name="ethereum_jsonrpc",
            content='{"result": "ok"}',
            tool_call_id="call_1",
        ),
    ]

    trace = extract_agent_trace(messages, max_preview_chars=500)

    assert len(trace) == 2
    assert trace[0].type == "tool_call_request"
    assert trace[0].tool == "ethereum_jsonrpc"
    assert trace[1].type == "tool_result"
    assert trace[1].tool == "ethereum_jsonrpc"


def test_extract_agent_trace_redacts_sensitive_fields():
    messages = [
        DummyAIMessage(
            tool_calls=[
                {
                    "id": "call_1",
                    "name": "some_tool",
                    "args": {"api_key": "secret_value", "method": "demo"},
                }
            ]
        )
    ]

    trace = extract_agent_trace(messages, max_preview_chars=500)

    assert len(trace) == 1
    assert "[REDACTED]" in trace[0].args_preview
    assert "secret_value" not in trace[0].args_preview


def test_extract_agent_trace_truncates_long_content():
    long_text = "x" * 1000
    messages = [
        DummyToolMessage(
            name="postgres_select",
            content=long_text,
            tool_call_id="call_1",
        )
    ]

    trace = extract_agent_trace(messages, max_preview_chars=100)

    assert len(trace) == 1
    assert trace[0].type == "tool_result"
    assert trace[0].content_preview.endswith("...[truncated]")


def test_extract_agent_trace_from_openai_raw_tool_calls():
    messages = [
        DummyAIMessage(
            tool_calls=None,
            additional_kwargs={
                "tool_calls": [
                    {
                        "id": "call_2",
                        "function": {
                            "name": "tron_node_request",
                            "arguments": '{"path":"/wallet/getnowblock"}',
                        },
                    }
                ]
            },
        )
    ]

    trace = extract_agent_trace(messages, max_preview_chars=500)

    assert len(trace) == 1
    assert trace[0].type == "tool_call_request"
    assert trace[0].tool == "tron_node_request"


def test_extract_agent_trace_with_invalid_tool_call():
    messages = [
        DummyAIMessage(
            invalid_tool_calls=[
                {
                    "id": "bad_1",
                    "name": "postgres_select",
                    "args": {"sql": "DROP TABLE users;"},
                    "error": "invalid tool call",
                }
            ]
        )
    ]

    trace = extract_agent_trace(messages, max_preview_chars=500)

    assert len(trace) == 1
    assert trace[0].type == "invalid_tool_call"
    assert trace[0].tool == "postgres_select"
    assert "invalid tool call" in trace[0].error_preview


def test_execution_trace_summary_uses_unified_event_buckets():
    state = {
        **new_execution_context("thread-1"),
        "status": "degraded",
        "node_events": [
            {"node_name": "router", "duration_ms": 1},
            {"node_name": "executor", "duration_ms": 2},
        ],
        "tool_events": [
            {"tool_call_id": "call-1", "attempt": 1, "status": "error"},
            {"tool_call_id": "call-1", "attempt": 2, "status": "success", "recovered": True},
        ],
        "decision_events": [
            {"decision_type": "permission_gate", "action": "allow"},
            {"decision_type": "evaluator", "action": "retry"},
        ],
        "error_events": [{"source": "tool", "error_type": "timeout"}],
        "context_events": [{"scene": "router", "trimmed": [{"category": "summary"}]}],
        "tool_result_records": [{"result_id": "raw-1", "tool_args": {"api_key": "secret"}}],
    }
    summary = build_request_summary(state)
    trace = execution_trace_from_state({**state, "request_summary": summary})

    assert trace["trace_id"] == state["trace_id"]
    assert summary["tool_calls"] == 1
    assert summary["tool_retries"] == 1
    assert summary["step_retries"] == 1
    assert summary["permission_checks"] == 1
    assert summary["errors"] == 1
    assert summary["context_builds"] == 1
    assert summary["context_trimmed_items"] == 1
    assert trace["context_events"][0]["scene"] == "router"
    assert trace["tool_result_records"][0]["result_id"] == "raw-1"
    assert trace["tool_result_records"][0]["tool_args"]["api_key"] == "[REDACTED]"
    assert summary["final_status"] == "degraded"
