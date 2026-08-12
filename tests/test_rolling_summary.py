from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from chaincloud_agent_service.agent.context_builder import ContextBuilder
from chaincloud_agent_service.agent.rolling_summary import (
    RollingSummaryManager,
    is_context_length_error,
    reactive_compact_retry,
)


SUMMARY = {
    "current_goal": "分析 0xabc 在 2026-01-01 至 2026-01-31 的风险",
    "confirmed_user_constraints": ["只使用链上确认数据"],
    "important_entities": ["0xabc"],
    "important_numbers": ["100 ETH"],
    "current_plan": {"step": "query"},
    "completed_steps": ["定位地址"],
    "pending_steps": ["查询交易"],
    "important_tool_findings": ["发现 100 ETH 转账"],
    "failed_attempts": ["Web 查询超时"],
    "unresolved_errors": ["RPC 数据待确认"],
    "permissions_approvals": ["只读查询已允许"],
    "clarified_state": {"chain": "ethereum"},
    "decisions_made": ["使用 RPC 作为主证据"],
    "open_questions": ["接收方身份未知"],
}


class WordCounter:
    method = "test_words"

    def text(self, value: str) -> int:
        return len(value.split())

    def message(self, message) -> int:  # type: ignore[no-untyped-def]
        return 1 + self.text(str(message.content))

    def messages(self, messages) -> int:  # type: ignore[no-untyped-def]
        return sum(self.message(message) for message in messages)


class SummaryModel:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def invoke(self, messages):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.fail:
            raise RuntimeError("summary unavailable")
        assert "messages_to_compact" in str(messages[-1].content)
        return AIMessage(content=json.dumps(SUMMARY, ensure_ascii=False))


def manager(max_input: int = 40) -> RollingSummaryManager:
    result = RollingSummaryManager(
        model_name="test", max_input_tokens=max_input, trigger_ratio=0.5,
        recent_messages=4, reactive_recent_messages=2, summary_input_tokens=100,
    )
    result.counter = WordCounter()  # type: ignore[assignment]
    return result


def long_state() -> dict:
    return {
        "messages": [HumanMessage(content=f"historical message number {index}") for index in range(12)],
        "plan": {"goal": "risk", "steps": []},
        "step_results": [], "clarified_state": {"chain": "ethereum"},
    }


def test_short_conversation_does_not_trigger() -> None:
    state = {"messages": [HumanMessage(content="short request")]}
    model = SummaryModel()

    assert manager().proactive_compact(state, model) == {}
    assert model.calls == 0


def test_long_conversation_automatically_triggers_by_tokens() -> None:
    state = long_state()
    model = SummaryModel()
    update = manager().proactive_compact(state, model)

    assert update["summary_version"] == 1
    assert update["summarized_until"] > 0
    assert update["compact_events"][-1]["status"] == "success"
    assert model.calls == 1


def test_old_messages_become_summary_plus_recent_window() -> None:
    state = long_state()
    rolling = manager()
    update = rolling.compact(state, SummaryModel())
    compacted = {**state, **update}
    active = rolling.active_messages(compacted)
    builder = ContextBuilder("test", 1000, 800, 100)
    builder.counter = WordCounter()  # type: ignore[assignment]
    context = builder.router(
        system_prompt="rules", current_request="latest", history=active,
        tool_names="none", conversation_summary=update["conversation_summary"],
    )

    assert len(active) == 4
    assert any("task-aware rolling summary" in str(message.content) for message in context.messages)
    assert any("只使用链上确认数据" in str(message.content) for message in context.messages)
    assert context.audit["category_tokens"]["summary_constraints"] > 0


def test_task_goal_and_constraints_survive_summary() -> None:
    update = manager().compact(long_state(), SummaryModel())

    assert update["conversation_summary"]["current_goal"].startswith("分析 0xabc")
    assert "只使用链上确认数据" in update["conversation_summary"]["confirmed_user_constraints"]
    assert update["conversation_summary"]["clarified_state"]["chain"] == "ethereum"


def test_compaction_keeps_full_checkpoint_messages() -> None:
    state = long_state()
    original = list(state["messages"])
    update = manager().compact(state, SummaryModel())

    assert state["messages"] == original
    assert "messages" not in update
    assert len(update["summarized_message_ids"]) == update["summarized_until"]


def test_summary_failure_preserves_old_summary_and_records_failure() -> None:
    state = {**long_state(), "conversation_summary": SUMMARY, "summary_version": 2}
    update = manager().compact(state, SummaryModel(fail=True))

    assert "conversation_summary" not in update
    assert state["conversation_summary"] == SUMMARY
    assert state["summary_version"] == 2
    assert update["compact_failure_count"] == 1
    assert update["compact_events"][-1]["status"] == "failed"


def test_compact_failure_circuit_breaker_stops_summary_calls() -> None:
    state = {**long_state(), "compact_failure_count": 3}
    model = SummaryModel()
    update = manager().compact(state, model)

    assert model.calls == 0
    assert update["compact_events"][-1]["reason"] == "compact_circuit_open"


def test_prompt_too_long_triggers_reactive_compact_and_one_retry() -> None:
    rolling = manager()
    calls = 0

    def call(context):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("maximum context length exceeded")
        return "ok"

    value, _, update = reactive_compact_retry(
        state=long_state(), manager=rolling, summary_model=SummaryModel(),
        build_context=lambda state: rolling.active_messages(state), call=call,
    )

    assert value == "ok"
    assert calls == 2
    assert update["compact_events"][-1]["mode"] == "reactive"


def test_context_builder_near_limit_triggers_proactive_compact() -> None:
    rolling = manager()
    summary_model = SummaryModel()

    class Built:
        def __init__(self, total: int) -> None:
            self.audit = {"total_tokens": total}

    value, _, update = reactive_compact_retry(
        state=long_state(), manager=rolling, summary_model=summary_model,
        build_context=lambda state: Built(39 if not state.get("conversation_summary") else 10),
        call=lambda context: "ok",
    )

    assert value == "ok"
    assert summary_model.calls == 1
    assert update["compact_events"][-1]["mode"] == "proactive"


def test_reactive_retry_is_limited_to_one() -> None:
    calls = 0

    def always_too_long(context):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        raise RuntimeError("prompt too long")

    with pytest.raises(RuntimeError, match="prompt too long"):
        reactive_compact_retry(
            state=long_state(), manager=manager(), summary_model=SummaryModel(),
            build_context=lambda state: state, call=always_too_long,
        )
    assert calls == 2


def test_context_length_error_detection() -> None:
    assert is_context_length_error(RuntimeError("context_length_exceeded"))
    assert not is_context_length_error(RuntimeError("timeout"))
