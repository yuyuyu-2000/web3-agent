from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from chaincloud_agent_service.agent.context_builder import (
    ContextBudgetError,
    ContextBuilder,
    ContextPart,
)


class WordCounter:
    method = "test_words"

    def text(self, value: str) -> int:
        return len(value.split())

    def message(self, message) -> int:  # type: ignore[no-untyped-def]
        return 1 + self.text(str(message.content))

    def messages(self, messages) -> int:  # type: ignore[no-untyped-def]
        return sum(self.message(message) for message in messages)


def builder(input_tokens: int = 20) -> ContextBuilder:
    result = ContextBuilder("test", 30, input_tokens, 10)
    result.counter = WordCounter()  # type: ignore[assignment]
    return result


def test_budget_trims_low_priority_context_first() -> None:
    result = builder().build(
        "router",
        [
            ContextPart("system", [SystemMessage(content="safe rules")], 1, True),
            ContextPart(
                "current_request", [HumanMessage(content="current request")], 2, True
            ),
            ContextPart(
                "memory", [SystemMessage(content="useful old memory words")], 6
            ),
            ContextPart(
                "recent_history",
                [
                    HumanMessage(content="recent one"),
                    HumanMessage(content="recent two"),
                ],
                7,
                newest_first=True,
            ),
            ContextPart(
                "summary", [SystemMessage(content="very old summary words here")], 8
            ),
        ],
    )

    assert result.audit["total_tokens"] <= 20
    assert result.audit["reserved_output_tokens"] == 10
    assert any(item["category"] == "summary" for item in result.audit["trimmed"])
    assert "safe rules" in [str(message.content) for message in result.messages]
    assert "current request" in [str(message.content) for message in result.messages]


def test_protected_context_is_never_silently_removed() -> None:
    with pytest.raises(ContextBudgetError, match="protected context"):
        builder(4).build(
            "reviewer",
            [
                ContextPart(
                    "system",
                    [SystemMessage(content="too many protected words")],
                    1,
                    True,
                )
            ],
        )


def test_router_keeps_only_eight_recent_non_tool_messages() -> None:
    history = [HumanMessage(content=f"message {index}") for index in range(12)]
    history.append(HumanMessage(content="current"))
    context_builder = ContextBuilder("test", 200, 150, 20)
    context_builder.counter = WordCounter()  # type: ignore[assignment]
    result = context_builder.router(
        system_prompt="router rules",
        current_request="current",
        history=history,
        tool_names="none",
    )
    recent = result.audit["category_tokens"]["recent_history"]
    assert recent == 8 * 3


def test_budget_respects_context_window_minus_reserved_output() -> None:
    result = ContextBuilder(
        "test", model_context_window=100, max_input_tokens=95, reserved_output_tokens=20
    )
    assert result.max_input_tokens == 80


def test_tool_request_and_result_are_trimmed_atomically() -> None:
    context_builder = builder(8)
    tool_request = AIMessage(
        content="", tool_calls=[{"id": "call-1", "name": "query", "args": {}}]
    )
    tool_result = ToolMessage(content="large tool result words", tool_call_id="call-1")
    result = context_builder.build(
        "direct_executor",
        [
            ContextPart("system", [SystemMessage(content="safe")], 1, True),
            ContextPart("current_request", [HumanMessage(content="request")], 2, True),
            ContextPart(
                "recent_history", [tool_request, tool_result], 7, newest_first=True
            ),
        ],
    )
    selected_types = [message.type for message in result.messages]
    assert ("ai" in selected_types) == ("tool" in selected_types)


def test_recent_window_does_not_orphan_tool_result_at_twelve_message_boundary() -> None:
    context_builder = ContextBuilder("test", 1000, 800, 100)
    context_builder.counter = WordCounter()  # type: ignore[assignment]
    tool_request = AIMessage(
        content="", tool_calls=[{"id": "call-1", "name": "query", "args": {}}]
    )
    tool_result = ToolMessage(content="result", tool_call_id="call-1")
    history = [tool_request, tool_result]
    history.extend(HumanMessage(content=f"later {index}") for index in range(11))
    history.append(HumanMessage(content="current"))

    result = context_builder.executor(
        scene="direct_executor",
        system_prompt="rules",
        current_request="current",
        critical_state="safe",
        messages=history,
    )

    assert tool_request not in result.messages
    assert tool_result not in result.messages
    assert not any(isinstance(message, ToolMessage) for message in result.messages)


def test_recent_window_drops_preexisting_orphan_tool_message() -> None:
    context_builder = ContextBuilder("test", 1000, 800, 100)
    orphan = ToolMessage(content="legacy result", tool_call_id="missing-call")

    result = context_builder.executor(
        scene="direct_executor",
        system_prompt="rules",
        current_request="current",
        critical_state="safe",
        messages=[orphan, HumanMessage(content="current")],
    )

    assert orphan not in result.messages


def test_memory_has_an_independent_token_cap() -> None:
    context_builder = ContextBuilder("test", 100, 80, 10, memory_max_tokens=4)
    context_builder.counter = WordCounter()  # type: ignore[assignment]
    first = SystemMessage(content="short memory")
    second = SystemMessage(content="another memory")
    result = context_builder.build(
        "direct_executor",
        [
            ContextPart("system", [SystemMessage(content="rules")], 1, True),
            ContextPart("memory", [first, second], 6),
            ContextPart("current_request", [HumanMessage(content="request")], 2, True),
        ],
    )
    assert first in result.messages
    assert second not in result.messages
