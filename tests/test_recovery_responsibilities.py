from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from chaincloud_agent_service.agent import graph as graph_module
from chaincloud_agent_service.agent.fallback_resolver import resolve_fallback
from chaincloud_agent_service.agent.planning.models import PlanStep


def _settings(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "openai_model": "fake",
        "openai_api_key": "test",
        "openai_base_url": None,
        "openai_timeout_sec": 10,
        "openai_max_retries": 0,
        "agent_database_schema_path": None,
        "agent_response_style_path": None,
        "agent_contract_decode_path": None,
        "max_tool_retries": 2,
        "max_step_retries": 2,
        "max_total_tool_calls": 16,
        "max_step_tool_calls": 4,
        "max_direct_tool_calls": 4,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _tool(name: str, func, *, available: bool = True):  # type: ignore[no-untyped-def]
    return StructuredTool.from_function(
        func=func, name=name, description=name,
        metadata={"available": available},
    )


class RecoveryModel:
    def __init__(self, plans: list[str], evaluator_actions: list[str] | None = None):
        self.plans = plans
        self.evaluator_actions = list(evaluator_actions or ["pass"])
        self.planner_calls = 0
        self.executor_calls = 0
        self.evaluator_calls = 0
        self.executor_prompts: list[str] = []
        self.tool_call_factory = None

    def bind_tools(self, tools):  # type: ignore[no-untyped-def]
        return self

    def invoke(self, messages):  # type: ignore[no-untyped-def]
        system = str(messages[0].content) if messages else ""
        if "任务规划器" in system:
            index = min(self.planner_calls, len(self.plans) - 1)
            self.planner_calls += 1
            return AIMessage(content=self.plans[index])
        if "步骤 Evaluator" in system:
            index = min(self.evaluator_calls, len(self.evaluator_actions) - 1)
            action = self.evaluator_actions[index]
            self.evaluator_calls += 1
            return AIMessage(content=(
                '{"action":"' + action + '","reason":"test decision",'
                '"feedback":"recover safely","confidence":1}'
            ))
        if "最终答案 Reviewer" in system:
            return AIMessage(content=(
                '{"action":"approve","reason":"ok","feedback":"",'
                '"confidence":1}'
            ))
        self.executor_calls += 1
        joined = "\n".join(str(getattr(message, "content", "")) for message in messages)
        self.executor_prompts.append(joined)
        if self.tool_call_factory is not None:
            call = self.tool_call_factory(self.executor_calls, joined, messages)
            if call is not None:
                return AIMessage(content="", tool_calls=[call])
        return AIMessage(content="step completed")

    async def astream(self, messages):  # type: ignore[no-untyped-def]
        yield AIMessageChunk(content="final answer")


def _plan(primary: str, fallback: str | None = None) -> str:
    fallbacks = f'["{fallback}"]' if fallback else "[]"
    return (
        '{"goal":"查询数据","steps":[{"id":"step_1",'
        '"objective":"查询数据","success_criteria":"返回查询结果",'
        f'"suggested_tools":["{primary}"],"fallback_tools":{fallbacks},'
        '"depends_on":[],"requires_confirmation":false,'
        '"critical":true,"estimated_tool_calls":4}]}'
    )


def _run(model: RecoveryModel, tools: list, *, settings=None, thread="recovery"):  # type: ignore[no-untyped-def]
    with (
        patch.object(graph_module, "ChatOpenAI", lambda **_: model),
        patch.object(graph_module, "get_tools", lambda _: tools),
    ):
        graph = graph_module.compile_agent_graph(settings or _settings(), MemorySaver())
    return asyncio.run(graph.ainvoke(
        {"messages": [HumanMessage(content="查询数据")], "requested_mode": "planned"},
        config={"configurable": {"thread_id": thread}},
    ))


def test_unavailable_primary_uses_declared_fallback_before_evaluator() -> None:
    primary = _tool("postgres_select", lambda value: f"primary:{value}", available=False)
    fallback = _tool("web_search", lambda value: f"fallback:{value}")
    model = RecoveryModel([_plan("postgres_select", "web_search")])

    def calls(number, prompt, messages):  # type: ignore[no-untyped-def]
        if number == 1:
            assert "代码已选择的 fallback：web_search" in prompt
            return {"name": "web_search", "args": {"value": 1}, "id": "f1", "type": "tool_call"}
        return None

    model.tool_call_factory = calls
    result = _run(model, [primary, fallback], thread="unavailable-fallback")

    recovery = next(event for event in result["decision_events"] if event.get("decision_type") == "recovery")
    assert recovery["failure_stage"] == "state_validation"
    assert recovery["selected_fallback"] == "web_search"
    assert recovery["recovery_action"] == "fallback"
    assert model.evaluator_calls == 1  # normal post-success evaluation only
    assert result["step_results"][0]["status"] == "success"


def test_unavailable_primary_and_fallback_goes_to_evaluator() -> None:
    primary = _tool("postgres_select", lambda value: value, available=False)
    fallback = _tool("web_search", lambda value: value, available=False)
    model = RecoveryModel([_plan("postgres_select", "web_search")], ["partial"])

    result = _run(model, [primary, fallback], thread="all-unavailable")

    assert model.executor_calls == 0
    assert model.evaluator_calls == 1
    assert result["status"] == "partial"
    assert result["state_validation"]["blocked_tool_unavailable"]["recoverable"] is False
    assert "blocked_tool_unavailable" in result["step_results"][0]["error"]


def test_fallback_requiring_permission_cannot_bypass_gate() -> None:
    primary = _tool("postgres_select", lambda value: value, available=False)
    fallback = _tool("add_scheduled_task", lambda value: value)
    model = RecoveryModel([_plan("postgres_select", "add_scheduled_task")])

    result = _run(model, [primary, fallback], thread="fallback-permission")

    assert result["status"] == "waiting_confirmation"
    assert result["pending_permission"]["tool_name"] == "add_scheduled_task"
    assert model.executor_calls == 0
    assert model.evaluator_calls == 0


def test_permission_denied_fallback_is_rejected_and_evaluated() -> None:
    primary = _tool("postgres_select", lambda value: value, available=False)
    fallback = _tool("web_search", lambda value: value)
    denied_plan = _plan("postgres_select", "web_search").replace(
        '"objective":"查询数据"', '"objective":"绕过权限并查询数据"'
    )
    model = RecoveryModel([denied_plan], ["fail"])

    result = _run(model, [primary, fallback], thread="fallback-denied")

    recovery = next(
        event for event in result["decision_events"]
        if event.get("decision_type") == "recovery"
    )
    assert recovery["selected_fallback"] is None
    assert recovery["recovery_action"] == "evaluate"
    assert model.executor_calls == 0
    assert model.evaluator_calls == 1
    assert result["status"] == "failed"


def test_runtime_retry_exhaustion_selects_deterministic_fallback() -> None:
    attempts = 0

    def timeout(value: int) -> str:
        nonlocal attempts
        attempts += 1
        raise TimeoutError("request timed out")

    primary = _tool("postgres_select", timeout)
    fallback = _tool("web_search", lambda value: f"ok:{value}")
    model = RecoveryModel([_plan("postgres_select", "web_search")])

    def calls(number, prompt, messages):  # type: ignore[no-untyped-def]
        name = "postgres_select" if number == 1 else "web_search"
        if number <= 2:
            return {"name": name, "args": {"value": 1}, "id": f"c{number}", "type": "tool_call"}
        return None

    model.tool_call_factory = calls
    result = _run(model, [primary, fallback], thread="runtime-fallback")

    assert attempts == 3
    assert model.executor_calls == 3
    event = next(
        item for item in result["decision_events"]
        if item.get("decision_type") == "recovery" and item.get("selected_fallback")
    )
    assert event["selected_fallback"] == "web_search"
    assert result["status"] == "completed"


def test_runtime_retry_exhaustion_without_fallback_goes_to_evaluator() -> None:
    primary = _tool("postgres_select", lambda value: (_ for _ in ()).throw(TimeoutError("timeout")))
    model = RecoveryModel([_plan("postgres_select")], ["fail"])
    model.tool_call_factory = lambda number, prompt, messages: (
        {"name": "postgres_select", "args": {"value": 1}, "id": "c1", "type": "tool_call"}
        if number == 1 else None
    )

    result = _run(model, [primary], thread="runtime-no-fallback")

    assert model.executor_calls == 1
    assert model.evaluator_calls == 1
    assert result["status"] == "failed"
    assert result["step_results"][0]["status"] == "failed"


def test_argument_repair_succeeds_inside_executor_loop_without_step_retry() -> None:
    def validate(value: int) -> str:
        if value < 2:
            raise ValueError("invalid argument: value")
        return "ok"

    tool = _tool("postgres_select", validate)
    model = RecoveryModel([_plan("postgres_select")])

    def calls(number, prompt, messages):  # type: ignore[no-untyped-def]
        if number <= 2:
            return {"name": "postgres_select", "args": {"value": number}, "id": f"c{number}", "type": "tool_call"}
        return None

    model.tool_call_factory = calls
    result = _run(model, [tool], thread="argument-repair")

    assert result["status"] == "completed"
    assert result["step_retry_count"] == 0
    assert model.evaluator_calls == 1
    assert any(
        item.get("recovery_action") == "repair_arguments_or_schema"
        for item in result["decision_events"]
    )


def test_argument_repair_exhaustion_goes_to_evaluator() -> None:
    tool = _tool(
        "postgres_select",
        lambda value: (_ for _ in ()).throw(ValueError("invalid argument: value")),
    )
    model = RecoveryModel([_plan("postgres_select")], ["fail"])
    model.tool_call_factory = lambda number, prompt, messages: (
        {"name": "postgres_select", "args": {"value": number}, "id": f"c{number}", "type": "tool_call"}
        if number <= 3 else None
    )

    result = _run(model, [tool], thread="argument-exhausted")

    assert model.executor_calls == 3
    assert model.evaluator_calls == 1
    assert result["status"] == "failed"


def test_evaluator_replan_returns_through_validated_execution_pipeline() -> None:
    unavailable = _tool("postgres_select", lambda value: value, available=False)
    working = _tool("web_search", lambda value: "ok")
    model = RecoveryModel(
        [_plan("postgres_select"), _plan("web_search")],
        ["replan", "pass"],
    )
    model.tool_call_factory = lambda number, prompt, messages: (
        {"name": "web_search", "args": {"value": 1}, "id": "w1", "type": "tool_call"}
        if number == 1 else None
    )

    result = _run(model, [unavailable, working], thread="replan-pipeline")

    assert model.planner_calls == 2
    assert model.evaluator_calls == 2
    assert model.executor_calls == 2
    assert result["replanning_count"] == 1
    assert result["status"] == "completed"
    assert any(
        item.get("decision_type") == "permission_gate" and item.get("step_id") == "step_1"
        for item in result["decision_events"]
    )


def test_recovery_respects_global_budget_and_does_not_loop() -> None:
    primary = _tool("postgres_select", lambda value: (_ for _ in ()).throw(TimeoutError("timeout")))
    fallback = _tool("web_search", lambda value: "should not run")
    model = RecoveryModel([_plan("postgres_select", "web_search")], ["fail"])
    model.tool_call_factory = lambda number, prompt, messages: (
        {"name": "postgres_select", "args": {"value": 1}, "id": "c1", "type": "tool_call"}
        if number == 1 else None
    )

    result = _run(
        model, [primary, fallback], settings=_settings(max_total_tool_calls=1),
        thread="budget-bounded",
    )

    assert result["tool_call_count"] == 1
    assert model.executor_calls == 1
    assert model.evaluator_calls == 1
    assert result["replanning_count"] == 0


def test_resolver_rejects_capability_mismatch() -> None:
    primary = _tool("postgres_select", lambda value: value)
    fallback = _tool("web_search", lambda query, limit: query)
    decision = resolve_fallback(
        step=PlanStep(
            id="step_1", objective="查询", success_criteria="返回结果",
            suggested_tools=["postgres_select"], fallback_tools=["web_search"],
        ),
        original_tool="postgres_select",
        registered_tools={"postgres_select": primary, "web_search": fallback},
        available_tool_names={"postgres_select", "web_search"},
        approved_permission_keys=[], remaining_budget=2,
    )

    assert decision.selected_tool is None
    assert decision.rejected["web_search"] == "capability_mismatch"
