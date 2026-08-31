from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from chaincloud_agent_service.agent import graph as graph_module
from chaincloud_agent_service.persistence.checkpoint import memory_checkpointer


TXID = "a1" * 32
DATE = "2026-08-06"
REQUIRED_FACTS = ("USDT", "amount_usd >= 100000", TXID, DATE)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        openai_model="fake-long-context",
        openai_api_key="test",
        openai_base_url=None,
        openai_timeout_sec=10,
        openai_max_retries=0,
        agent_database_schema_path=None,
        agent_response_style_path=None,
        agent_contract_decode_path=None,
        model_context_window=2_400,
        max_input_tokens=1_800,
        reserved_output_tokens=300,
        rolling_summary_trigger_ratio=0.10,
        rolling_summary_recent_messages=8,
        rolling_summary_reactive_recent_messages=4,
        rolling_summary_max_input_tokens=1_000,
        rolling_summary_max_failures=3,
        memory_recall_context_tokens=100,
    )


class DeterministicLongContextModel:
    """External-LLM substitute; graph/context/checkpoint code remains production code."""

    summary_calls = 0
    final_contexts: list[str] = []

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        pass

    def bind_tools(self, tools):  # type: ignore[no-untyped-def]
        return self

    def invoke(self, messages):  # type: ignore[no-untyped-def]
        rendered = "\n".join(str(message.content) for message in messages)
        if "线程上下文压缩器" in rendered:
            type(self).summary_calls += 1
            # Fail loudly if the production compaction prompt no longer contains
            # either the original facts or a previous summary carrying them.
            assert all(fact in rendered for fact in REQUIRED_FACTS)
            return AIMessage(
                content=json.dumps(
                    {
                        "current_goal": "持续分析指定日期的大额 USDT 交易",
                        "confirmed_user_constraints": [
                            "后续分析只看 USDT",
                            "只关注 amount_usd >= 100000",
                            f"日期固定为 {DATE}",
                        ],
                        "important_entities": [TXID, "USDT"],
                        "important_numbers": ["100000", DATE],
                        "current_plan": {"step": "continue_analysis"},
                        "completed_steps": [],
                        "pending_steps": ["按既有约束继续任务"],
                        "important_tool_findings": [],
                        "failed_attempts": [],
                        "unresolved_errors": [],
                        "permissions_approvals": ["仅只读分析"],
                        "clarified_state": {
                            "token": "USDT",
                            "minimum_amount_usd": 100000,
                            "txid": TXID,
                            "date": DATE,
                        },
                        "decisions_made": ["不分析其他 Token"],
                        "open_questions": [],
                    },
                    ensure_ascii=False,
                )
            )
        return AIMessage(content="本轮已记录，继续沿用既有约束。")

    async def astream(self, messages):  # type: ignore[no-untyped-def]
        rendered = "\n".join(str(message.content) for message in messages)
        type(self).final_contexts.append(rendered)
        if "最终核对" in rendered:
            retained = [fact for fact in REQUIRED_FACTS if fact in rendered]
            answer = "；".join(retained) if len(retained) == len(REQUIRED_FACTS) else "CONTEXT_FACTS_MISSING"
        else:
            answer = "已完成当前轮次。"
        input_tokens = len(rendered)
        output_tokens = len(answer)
        yield AIMessageChunk(
            content=answer,
            usage_metadata={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        )


def run_long_context_scenario(rounds: int) -> dict:
    DeterministicLongContextModel.summary_calls = 0
    DeterministicLongContextModel.final_contexts = []
    checkpointer = memory_checkpointer()
    with (
        patch.object(graph_module, "ChatOpenAI", DeterministicLongContextModel),
        patch.object(graph_module, "get_tools", lambda settings: []),
    ):
        graph = graph_module.compile_agent_graph(_settings(), checkpointer)

    thread_id = f"long-context-{rounds}-rounds"
    config = {"configurable": {"thread_id": thread_id}}
    initial = (
        "请长期记住以下分析约束：后续分析只看 USDT；"
        "只关注 amount_usd >= 100000；"
        f"固定 txid 为 {TXID}；日期固定为 {DATE}；仅做只读分析。"
    )
    final_state = None
    for index in range(1, rounds + 1):
        if index == 1:
            content = initial
        elif index == rounds:
            content = "最终核对：请根据之前保存的信息列出 Token、金额阈值、固定 txid 和日期，并继续任务。"
        else:
            content = (
                f"这是第 {index} 轮。继续此前分析，不改变任何筛选条件。"
                "补充背景文本用于形成真实的长会话上下文，但不得替换最初约束。"
            )
        final_state = asyncio.run(
            graph.ainvoke(
                {"messages": [HumanMessage(content=content)], "requested_mode": "direct"},
                config=config,
            )
        )

    assert final_state is not None
    snapshot = graph.get_state(config)
    checkpoint_state = dict(snapshot.values)
    summary = checkpoint_state.get("conversation_summary") or {}
    summary_text = json.dumps(summary, ensure_ascii=False, default=str)
    final_answer = str(checkpoint_state["messages"][-1].content)
    compact_events = list(checkpoint_state.get("compact_events", []))
    context_events = list(checkpoint_state.get("context_events", []))
    successful_compactions = [event for event in compact_events if event.get("status") == "success"]
    final_context_event = context_events[-1]
    return {
        "rounds": rounds,
        "thread_id": thread_id,
        "checkpoint_message_count": len(checkpoint_state.get("messages", [])),
        "conversation_summary": summary,
        "summary_version": checkpoint_state.get("summary_version", 0),
        "summarized_until": checkpoint_state.get("summarized_until", 0),
        "summarized_message_ids_count": len(checkpoint_state.get("summarized_message_ids", [])),
        "compact_events": compact_events,
        "context_events": context_events,
        "summary_model_calls": DeterministicLongContextModel.summary_calls,
        "rolling_summary_triggered": bool(successful_compactions),
        "compression_count": len(successful_compactions),
        "key_fact_retention": all(fact in summary_text for fact in ("USDT", TXID, DATE)),
        "constraint_retention": (
            "amount_usd >= 100000" in summary_text and "只看 USDT" in summary_text
        ),
        "final_context_contains_all_facts": all(
            fact in DeterministicLongContextModel.final_contexts[-1] for fact in REQUIRED_FACTS
        ),
        "final_input_tokens": final_context_event["total_tokens"],
        "max_input_tokens": final_context_event["max_input_tokens"],
        "final_answer": final_answer,
        "final_status": checkpoint_state.get("status"),
        "final_task_success": (
            checkpoint_state.get("status") == "completed"
            and all(fact in final_answer for fact in REQUIRED_FACTS)
        ),
    }


@pytest.mark.parametrize("rounds", [30, 50])
def test_long_thread_rolls_summary_retains_constraints_and_bounds_context(rounds: int) -> None:
    result = run_long_context_scenario(rounds)

    assert result["rolling_summary_triggered"] is True
    assert result["compression_count"] >= 1
    assert result["summarized_until"] > 0
    assert result["summarized_message_ids_count"] == result["summarized_until"]
    assert result["key_fact_retention"] is True
    assert result["constraint_retention"] is True
    assert result["final_context_contains_all_facts"] is True
    assert result["final_input_tokens"] <= result["max_input_tokens"]
    assert result["final_task_success"] is True
