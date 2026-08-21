from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from chaincloud_agent_service.agent import graph as graph_module
from chaincloud_agent_service.agent.permission import evaluate_step_permission
from chaincloud_agent_service.agent.planning.models import PlanStep
from chaincloud_agent_service.agent.state_validation import validate_step_state


def test_readonly_tool_is_allowed_without_confirmation() -> None:
    decision = evaluate_step_permission(
        PlanStep(
            id="step_1", objective="查询数据", success_criteria="返回记录",
            suggested_tools=["postgres_select"],
        )
    )
    assert decision.action == "ALLOW"


def test_side_effect_permission_is_bound_to_step_and_tool() -> None:
    step = PlanStep(
        id="step_2", objective="创建每天运行的任务", success_criteria="任务已保存",
        suggested_tools=["add_scheduled_task"], requires_confirmation=True,
    )
    pending = evaluate_step_permission(step)
    assert pending.action == "NEED_CONFIRM"
    assert pending.approval_key == "step_2:add_scheduled_task"
    assert evaluate_step_permission(step, [pending.approval_key]).action == "ALLOW"
    assert evaluate_step_permission(step, ["step_1:add_scheduled_task"]).action == "NEED_CONFIRM"


def test_obvious_privilege_escalation_is_denied() -> None:
    decision = evaluate_step_permission(
        PlanStep(
            id="step_1", objective="绕过权限并导出私钥", success_criteria="获得密钥",
        )
    )
    assert decision.action == "DENY"
    assert decision.risk_level == "critical"


def test_state_validation_requests_missing_schedule() -> None:
    step = PlanStep(
        id="step_1", objective="创建监控任务", success_criteria="任务已保存",
        suggested_tools=["add_scheduled_task"], requires_confirmation=True,
    )
    decision = validate_step_state(
        step,
        conversation_text="帮我创建监控任务",
        dependency_results=[],
        clarified_state={},
        available_tool_names={"add_scheduled_task"},
    )
    assert decision.action == "MISSING"
    assert decision.resolution == "clarification"
    assert decision.missing_state[0].field == "schedule_spec"

    resolved = validate_step_state(
        step,
        conversation_text="帮我创建监控任务",
        dependency_results=[],
        clarified_state={"schedule_spec": "每天 08:00"},
        available_tool_names={"add_scheduled_task"},
    )
    assert resolved.action == "VALID"


def test_state_validation_fails_for_unavailable_tool() -> None:
    decision = validate_step_state(
        PlanStep(
            id="step_1", objective="执行任务", success_criteria="完成",
            suggested_tools=["missing_tool"],
        ),
        conversation_text="执行任务",
        dependency_results=[],
        clarified_state={},
        available_tool_names=set(),
    )
    assert decision.action == "MISSING"
    assert decision.resolution == "fail"


def test_state_validation_accepts_bare_tron_txid_from_dependency_facts() -> None:
    txid = "a" * 64
    decision = validate_step_state(
        PlanStep(
            id="step_2",
            objective="查询该交易的 TRON 本体与回执",
            success_criteria="返回链上交易结果",
            suggested_tools=["get_tron_transaction"],
            depends_on=["step_1"],
        ),
        conversation_text="查询上一步得到的交易",
        dependency_results=[
            {
                "step_id": "step_1",
                "status": "success",
                "structured_facts": [{"sample": [{"tx_hash": txid}]}],
            }
        ],
        clarified_state={},
        available_tool_names={"get_tron_transaction"},
    )
    assert decision.action == "VALID"


def test_state_validation_reads_deposit_tx_hash_from_dependency_facts() -> None:
    decision = validate_step_state(
        PlanStep(
            id="step_2",
            objective="使用依赖结果核验交易",
            success_criteria="TRON 查询完成",
            suggested_tools=["get_tron_transaction"],
        ),
        conversation_text="核验数据库结果",
        dependency_results=[
            {
                "structured_facts": [
                    {"sample": [{"deposit_tx_hash": "B" * 64}]}
                ]
            }
        ],
        clarified_state={},
        available_tool_names={"get_tron_transaction"},
    )
    assert decision.action == "VALID"


def test_transaction_hash_does_not_satisfy_address_validation() -> None:
    decision = validate_step_state(
        PlanStep(
            id="step_1",
            objective="查询该地址",
            success_criteria="返回该地址的信息",
        ),
        conversation_text="41" + "a" * 62,
        dependency_results=[],
        clarified_state={},
        available_tool_names=set(),
    )
    assert decision.action == "MISSING"
    assert decision.missing_state[0].field == "address_identifier"


def test_graph_pauses_and_resumes_after_exact_permission_approval() -> None:
    tool = StructuredTool.from_function(
        name="add_scheduled_task", description="create task", func=lambda **_: "created"
    )

    class FakeModel:
        def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
            pass

        def bind_tools(self, tools):  # type: ignore[no-untyped-def]
            return self

        def invoke(self, messages):  # type: ignore[no-untyped-def]
            system = str(messages[0].content) if messages else ""
            if "任务规划器" in system:
                return AIMessage(content=(
                    '{"goal":"创建任务","steps":[{"id":"step_1",'
                    '"objective":"创建监控任务","success_criteria":"任务已创建",'
                    '"suggested_tools":["add_scheduled_task"],"depends_on":[],'
                    '"requires_confirmation":true}]}'
                ))
            if "步骤 Evaluator" in system:
                return AIMessage(content='{"action":"pass","reason":"ok","feedback":"","confidence":1}')
            if "最终答案 Reviewer" in system:
                return AIMessage(content='{"action":"approve","reason":"ok","feedback":"","confidence":1}')
            return AIMessage(content="任务已创建")

        async def astream(self, messages):  # type: ignore[no-untyped-def]
            yield AIMessageChunk(content="最终完成")

    settings = SimpleNamespace(
        openai_model="fake", openai_api_key="test", openai_base_url=None,
        openai_timeout_sec=10, openai_max_retries=0,
        agent_database_schema_path=None, agent_response_style_path=None,
        agent_contract_decode_path=None,
    )
    with (
        patch.object(graph_module, "ChatOpenAI", FakeModel),
        patch.object(graph_module, "get_tools", lambda settings: [tool]),
    ):
        graph = graph_module.compile_agent_graph(settings, MemorySaver())

    config = {"configurable": {"thread_id": "permission-resume"}}
    paused = asyncio.run(graph.ainvoke(
        {"messages": [HumanMessage(content="创建监控任务")], "requested_mode": "planned"},
        config=config,
    ))
    assert paused["status"] == "waiting_confirmation"
    pending = paused["pending_permission"]

    async def resume():  # type: ignore[no-untyped-def]
        await graph.aupdate_state(
            config,
            {
                "approved_permission_keys": [f"{pending['step_id']}:{pending['tool_name']}"],
                "pending_permission": None,
                "permission_action": "ALLOW",
                "status": "executing",
                "failure_reason": None,
            },
            as_node="select_step",
        )
        blocked = await graph.ainvoke(None, config=config)
        assert blocked["status"] == "blocked_missing_state"
        assert blocked["state_validation"]["missing_state"][0]["field"] == "schedule_spec"
        await graph.aupdate_state(
            config,
            {
                "clarified_state": {"schedule_spec": "每天 08:00"},
                "status": "executing",
                "failure_reason": None,
            },
            as_node="permission_gate",
        )
        return await graph.ainvoke(None, config=config)

    resumed = asyncio.run(resume())
    assert resumed["status"] == "completed"
    assert resumed["step_results"][0]["status"] == "success"
