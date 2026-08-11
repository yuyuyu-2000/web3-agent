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
