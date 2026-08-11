from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from chaincloud_agent_service.agent import graph as graph_module


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        openai_model="fake",
        openai_api_key="test",
        openai_base_url=None,
        openai_timeout_sec=10,
        openai_max_retries=0,
        agent_database_schema_path=None,
        agent_response_style_path=None,
        agent_contract_decode_path=None,
    )


def test_graph_switches_between_direct_and_planned_on_same_thread() -> None:
    class FakeModel:
        def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        def bind_tools(self, tools):  # type: ignore[no-untyped-def]
            return self

        def invoke(self, messages):  # type: ignore[no-untyped-def]
            system = str(messages[0].content) if messages else ""
            if "任务规划器" in system:
                return AIMessage(
                    content=(
                        '{"goal":"复杂分析","steps":[{"id":"step_1",'
                        '"objective":"完成分析","success_criteria":"形成结论",'
                        '"suggested_tools":[],"depends_on":[], '
                        '"requires_confirmation":false}]}'
                    )
                )
            return AIMessage(content="执行完成")

        async def astream(self, messages):  # type: ignore[no-untyped-def]
            yield AIMessageChunk(content="最终回答")

    with (
        patch.object(graph_module, "ChatOpenAI", FakeModel),
        patch.object(graph_module, "get_tools", lambda settings: []),
    ):
        graph = graph_module.compile_agent_graph(_settings(), MemorySaver())

    config = {"configurable": {"thread_id": "route-switch"}}
    direct = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="简单回答")],
                "requested_mode": "direct",
            },
            config=config,
        )
    )
    planned = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="复杂分析")],
                "requested_mode": "planned",
            },
            config=config,
        )
    )

    assert direct["execution_mode"] == "direct"
    assert direct["plan"] is None
    assert direct["status"] == "completed"
    assert planned["execution_mode"] == "planned"
    assert planned["status"] == "completed"
    assert len(planned["step_results"]) == 1
    assert planned["direct_tool_call_count"] == 0


def test_high_risk_direct_answer_is_reviewed_and_revised() -> None:
    class FakeModel:
        def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            self.review_calls = 0
            self.compose_calls = 0

        def bind_tools(self, tools):  # type: ignore[no-untyped-def]
            return self

        def invoke(self, messages):  # type: ignore[no-untyped-def]
            system = str(messages[0].content) if messages else ""
            if "最终答案 Reviewer" in system:
                self.review_calls += 1
                if self.review_calls == 1:
                    return AIMessage(
                        content=(
                            '{"action":"revise","reason":"缺少证据限定",'
                            '"feedback":"明确这是待验证推断","confidence":0.9}'
                        )
                    )
                return AIMessage(
                    content=(
                        '{"action":"approve","reason":"修订后符合要求",'
                        '"feedback":"","confidence":0.95}'
                    )
                )
            return AIMessage(content="该地址一定存在清算风险。")

        async def astream(self, messages):  # type: ignore[no-untyped-def]
            self.compose_calls += 1
            content = (
                "该地址可能存在清算风险，仍需链上证据验证。"
                if self.compose_calls > 1
                else "该地址一定存在清算风险。"
            )
            yield AIMessageChunk(content=content)

    with (
        patch.object(graph_module, "ChatOpenAI", FakeModel),
        patch.object(graph_module, "get_tools", lambda settings: []),
    ):
        graph = graph_module.compile_agent_graph(_settings(), MemorySaver())

    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="分析这个地址的清算风险")],
                "requested_mode": "direct",
            },
            config={"configurable": {"thread_id": "direct-review"}},
        )
    )

    assert result["execution_mode"] == "direct"
    assert result["review_required"] is True
    assert result["review_attempts"] == 2
    assert result["review_action"] == "approve"
    assert "仍需链上证据验证" in result["messages"][-1].content


def test_planned_step_retries_after_evaluator_feedback() -> None:
    class FakeModel:
        def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            self.executor_calls = 0
            self.evaluator_calls = 0

        def bind_tools(self, tools):  # type: ignore[no-untyped-def]
            return self

        def invoke(self, messages):  # type: ignore[no-untyped-def]
            system = str(messages[0].content) if messages else ""
            if "任务规划器" in system:
                return AIMessage(
                    content=(
                        '{"goal":"查询交易","steps":[{"id":"step_1",'
                        '"objective":"查询交易","success_criteria":"包含区块高度",'
                        '"suggested_tools":[],"depends_on":[], '
                        '"requires_confirmation":false}]}'
                    )
                )
            if "步骤 Evaluator" in system:
                self.evaluator_calls += 1
                if self.evaluator_calls == 1:
                    return AIMessage(
                        content=(
                            '{"action":"retry","reason":"缺少区块高度",'
                            '"feedback":"补充区块高度","confidence":0.95}'
                        )
                    )
                return AIMessage(
                    content=(
                        '{"action":"pass","reason":"满足标准",'
                        '"feedback":"","confidence":0.98}'
                    )
                )
            if "最终答案 Reviewer" in system:
                return AIMessage(
                    content=(
                        '{"action":"approve","reason":"证据一致",'
                        '"feedback":"","confidence":0.9}'
                    )
                )
            self.executor_calls += 1
            return AIMessage(
                content=(
                    "交易成功。"
                    if self.executor_calls == 1
                    else "交易成功，区块高度为 100。"
                )
            )

        async def astream(self, messages):  # type: ignore[no-untyped-def]
            yield AIMessageChunk(content="最终回答")

    with (
        patch.object(graph_module, "ChatOpenAI", FakeModel),
        patch.object(graph_module, "get_tools", lambda settings: []),
    ):
        graph = graph_module.compile_agent_graph(_settings(), MemorySaver())

    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [HumanMessage(content="查询交易")],
                "requested_mode": "planned",
            },
            config={"configurable": {"thread_id": "evaluator-retry"}},
        )
    )

    assert result["status"] == "completed"
    assert result["step_retry_count"] == 1
    assert len(result["step_results"]) == 1
    assert "区块高度为 100" in result["step_results"][0]["summary"]
