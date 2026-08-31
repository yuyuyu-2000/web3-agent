from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from chaincloud_agent_service.agent import graph as graph_module
from chaincloud_agent_service.memory import InMemoryMemoryStore, MemoryService
from chaincloud_agent_service.persistence.checkpoint import memory_checkpointer


class IsolationModel:
    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        pass

    def bind_tools(self, tools):  # type: ignore[no-untyped-def]
        return self

    @staticmethod
    def _render(messages) -> str:  # type: ignore[no-untyped-def]
        return "\n".join(str(message.content) for message in messages)

    def invoke(self, messages):  # type: ignore[no-untyped-def]
        rendered = self._render(messages)
        system = str(messages[0].content) if messages else ""
        if "任务规划器" in system:
            if "创建定时任务" in rendered:
                return AIMessage(content=json.dumps({
                    "goal": "创建定时任务",
                    "steps": [{
                        "id": "permission_step", "objective": "创建定时任务",
                        "success_criteria": "任务已创建",
                        "suggested_tools": ["add_scheduled_task"],
                        "depends_on": [], "requires_confirmation": True,
                    }],
                }, ensure_ascii=False))
            marker = "ALPHA" if "ALPHA" in rendered else "BETA"
            return AIMessage(content=json.dumps({
                "goal": f"完成 {marker}",
                "steps": [{
                    "id": f"step_{marker.lower()}",
                    "objective": f"执行 {marker}",
                    "success_criteria": f"返回 {marker}",
                    "suggested_tools": [], "depends_on": [],
                    "requires_confirmation": False,
                }],
            }, ensure_ascii=False))
        if "步骤 Evaluator" in system:
            return AIMessage(content='{"action":"pass","reason":"ok","feedback":"","confidence":1}')
        if "最终答案 Reviewer" in system:
            return AIMessage(content='{"action":"approve","reason":"ok","feedback":"","confidence":1}')
        markers = [value for value in ("USDT", "TRX", "ALPHA", "BETA") if value in rendered]
        return AIMessage(content="EXEC:" + ",".join(markers))

    async def astream(self, messages):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.01)
        rendered = self._render(messages)
        markers = [value for value in ("USDT", "TRX", "ALPHA", "BETA") if value in rendered]
        answer = "FINAL:" + ",".join(markers)
        yield AIMessageChunk(content=answer)


class TopicEmbeddings:
    def embed_query(self, text: str) -> list[float]:
        if "USDT" in text:
            return [1.0, 0.0]
        if "TRX" in text:
            return [0.0, 1.0]
        return [0.5, 0.5]


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        openai_model="fake-isolation", openai_api_key="test",
        openai_base_url=None, openai_timeout_sec=10, openai_max_retries=0,
        agent_database_schema_path=None, agent_response_style_path=None,
        agent_contract_decode_path=None,
    )


def build_graph():  # type: ignore[no-untyped-def]
    tool = StructuredTool.from_function(
        name="add_scheduled_task", description="create scheduled task",
        func=lambda **_: "created",
    )
    checkpointer = memory_checkpointer()
    with (
        patch.object(graph_module, "ChatOpenAI", IsolationModel),
        patch.object(graph_module, "get_tools", lambda settings: [tool]),
    ):
        graph = graph_module.compile_agent_graph(_settings(), checkpointer)
    return graph


def _snapshot(graph, thread_id: str) -> dict:  # type: ignore[no-untyped-def]
    return dict(graph.get_state({"configurable": {"thread_id": thread_id}}).values)


def _texts(state: dict) -> list[str]:
    return [str(getattr(message, "content", "")) for message in state.get("messages", [])]


async def evaluate_case_1() -> dict:
    graph = build_graph()

    async def run(user: str, thread: str, token: str):
        return await graph.ainvoke(
            {
                "messages": [HumanMessage(content=f"设置约束：只看 {token}，并复述该约束")],
                "requested_mode": "direct", "user_id": user,
                "trace_id": f"trace-{user}", "trace_thread_id": thread,
            },
            config={"configurable": {"thread_id": thread}},
        )

    state_a, state_b = await asyncio.gather(
        run("user-a", "thread-a", "USDT"),
        run("user-b", "thread-b", "TRX"),
    )
    checkpoint_a, checkpoint_b = _snapshot(graph, "thread-a"), _snapshot(graph, "thread-b")
    text_a, text_b = "\n".join(_texts(checkpoint_a)), "\n".join(_texts(checkpoint_b))
    leaked = "TRX" in text_a or "USDT" in text_b
    return {
        "case": "case_1_different_user_different_thread",
        "concurrent": True, "leaked": leaked,
        "result_statuses": [state_a.get("status"), state_b.get("status")],
        "checkpoint_a": checkpoint_a, "checkpoint_b": checkpoint_b,
    }


async def evaluate_case_2() -> dict:
    graph = build_graph()
    service = MemoryService(InMemoryMemoryStore(), TopicEmbeddings())
    service.save_memory(
        memory_key="alice-usdt", summary="Alice 私有约束：只看 USDT",
        source_thread_id="alice-source", user_id="user-a",
        metadata={"user_id": "user-a"},
    )
    service.save_memory(
        memory_key="bob-trx", summary="Bob 私有约束：只看 TRX",
        source_thread_id="bob-source", user_id="user-b",
        metadata={"user_id": "user-b"},
    )

    async def recall_and_run(user: str, token: str):
        candidates = await asyncio.to_thread(
            service.recall_memories,
            user_id=user, query=f"继续之前的 {token} 分析",
            min_similarity=0.7, candidate_limit=5, selected_limit=3,
        )
        records = [candidate.record for candidate in candidates]
        recalled = [{
            "memory_key": record.memory_key, "summary": record.summary,
            "metadata": record.metadata, "user_id": record.user_id,
        } for record in records]
        messages = [service.build_memory_system_message_for_record(record) for record in records]
        messages.append(HumanMessage(content=f"继续之前的 {token} 分析"))
        result = await graph.ainvoke(
            {
                "messages": messages, "requested_mode": "direct", "user_id": user,
                "recalled_memory_keys": [record.memory_key for record in records],
                "active_recalled_memories": recalled,
                "memory_recall_query": f"继续之前的 {token} 分析",
                "trace_id": f"trace-memory-{user}", "trace_thread_id": "shared-memory-thread",
            },
            config={"configurable": {"thread_id": "shared-memory-thread"}},
        )
        return {
            "user_id": user,
            "selected_keys": [record.memory_key for record in records],
            "active_recalled_memories": recalled,
            "result_recalled_memory_keys": result.get("recalled_memory_keys", []),
            "result_active_recalled_memories": result.get("active_recalled_memories", []),
        }

    alice, bob = await asyncio.gather(
        recall_and_run("user-a", "USDT"), recall_and_run("user-b", "TRX")
    )
    checkpoint = _snapshot(graph, "shared-memory-thread")
    service_isolated = alice["selected_keys"] == ["alice-usdt"] and bob["selected_keys"] == ["bob-trx"]
    result_isolated = (
        alice["result_recalled_memory_keys"] == ["alice-usdt"]
        and bob["result_recalled_memory_keys"] == ["bob-trx"]
    )
    return {
        "case": "case_2_different_user_same_thread_memory",
        "concurrent": True,
        "leaked": not (service_isolated and result_isolated),
        "memory_service_isolated": service_isolated,
        "per_request_result_isolated": result_isolated,
        "alice": alice, "bob": bob, "final_checkpoint": checkpoint,
    }


async def evaluate_case_3() -> dict:
    graph = build_graph()

    async def permission_thread():
        return await graph.ainvoke(
            {
                "messages": [HumanMessage(content="创建定时任务")],
                "requested_mode": "planned", "user_id": "user-a",
                "trace_id": "trace-permission", "trace_thread_id": "permission-thread",
            }, config={"configurable": {"thread_id": "permission-thread"}},
        )

    async def readonly_thread():
        return await graph.ainvoke(
            {
                "messages": [HumanMessage(content="普通只读任务：只看 USDT")],
                "requested_mode": "direct", "user_id": "user-b",
                "trace_id": "trace-readonly", "trace_thread_id": "readonly-thread",
            }, config={"configurable": {"thread_id": "readonly-thread"}},
        )

    pending_result, readonly_result = await asyncio.gather(permission_thread(), readonly_thread())
    pending_cp = _snapshot(graph, "permission-thread")
    readonly_cp = _snapshot(graph, "readonly-thread")
    leaked = bool(readonly_cp.get("pending_permission")) or readonly_cp.get("permission_action") in {"NEED_CONFIRM", "DENY"}
    return {
        "case": "case_3_permission_and_readonly_different_threads",
        "concurrent": True, "leaked": leaked,
        "pending_result_status": pending_result.get("status"),
        "readonly_result_status": readonly_result.get("status"),
        "permission_checkpoint": pending_cp, "readonly_checkpoint": readonly_cp,
    }


async def evaluate_case_4() -> dict:
    graph = build_graph()
    thread_id = "same-user-same-thread"

    async def run(marker: str):
        return await graph.ainvoke(
            {
                "messages": [HumanMessage(content=f"并发任务 {marker}")],
                "requested_mode": "planned", "user_id": "user-a",
                "trace_id": f"trace-{marker.lower()}", "trace_thread_id": thread_id,
            }, config={"configurable": {"thread_id": thread_id}},
        )

    result_alpha, result_beta = await asyncio.gather(run("ALPHA"), run("BETA"))
    checkpoint = _snapshot(graph, thread_id)
    texts = "\n".join(_texts(checkpoint))
    plan = checkpoint.get("plan") or {}
    step_results = checkpoint.get("step_results") or []
    trace_ids = sorted({
        str(event.get("trace_id")) for name in ("node_events", "decision_events", "tool_events", "error_events")
        for event in checkpoint.get(name, []) if event.get("trace_id")
    })
    both_messages = "ALPHA" in texts and "BETA" in texts
    both_step_results = "ALPHA" in json.dumps(step_results) and "BETA" in json.dumps(step_results)
    return {
        "case": "case_4_same_user_same_thread_concurrent",
        "concurrent": True,
        "both_messages_preserved": both_messages,
        "both_step_results_preserved": both_step_results,
        "final_plan": plan,
        "pending_permission": checkpoint.get("pending_permission"),
        "trace_ids_in_checkpoint": trace_ids,
        "result_alpha_status": result_alpha.get("status"),
        "result_beta_status": result_beta.get("status"),
        "result_alpha": result_alpha,
        "result_beta": result_beta,
        "final_checkpoint": checkpoint,
        "leaked_or_overwritten": not (both_messages and both_step_results),
    }


def run_all_concurrent_evaluations() -> list[dict]:
    async def run():
        return [
            await evaluate_case_1(),
            await evaluate_case_2(),
            await evaluate_case_3(),
            await evaluate_case_4(),
        ]
    return asyncio.run(run())


def test_concurrent_state_isolation_evaluation_executes_all_cases() -> None:
    results = run_all_concurrent_evaluations()
    assert len(results) == 4
    assert all(result["concurrent"] for result in results)
    assert results[0]["leaked"] is False
    assert results[1]["memory_service_isolated"] is True
    assert results[2]["leaked"] is False
