from __future__ import annotations

from langchain_core.messages import AIMessage

from chaincloud_agent_service.agent.planning.models import Plan, PlanStep
from chaincloud_agent_service.agent.planning.planner import create_plan
from chaincloud_agent_service.agent.planning.validator import (
    PlanValidationError,
    validate_plan,
)


class _Tool:
    name = "chain_query"
    description = "查询链上数据"


class _StaticModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    def invoke(self, messages):  # type: ignore[no-untyped-def]
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return AIMessage(content=response)


def test_validate_plan_accepts_ordered_dependencies() -> None:
    plan = Plan(
        goal="分析地址",
        steps=[
            PlanStep(
                id="step_1",
                objective="查询地址",
                success_criteria="取得地址数据",
                suggested_tools=["chain_query"],
            ),
            PlanStep(
                id="step_2",
                objective="总结风险",
                success_criteria="形成风险结论",
                depends_on=["step_1"],
            ),
        ],
    )

    assert validate_plan(plan, {"chain_query"}) == plan


def test_validate_plan_rejects_cyclic_dependencies() -> None:
    plan = Plan(
        goal="循环计划",
        steps=[
            PlanStep(
                id="step_1",
                objective="第一步",
                success_criteria="完成",
                depends_on=["step_2"],
            ),
            PlanStep(
                id="step_2",
                objective="第二步",
                success_criteria="完成",
                depends_on=["step_1"],
            ),
        ],
    )

    try:
        validate_plan(plan, set())
    except PlanValidationError as exc:
        assert "循环依赖" in str(exc)
    else:
        raise AssertionError("cyclic plan should be rejected")


def test_create_plan_retries_invalid_output() -> None:
    model = _StaticModel(
        [
            "not-json",
            '{"goal":"查询交易","steps":[{"id":"step_1",'
            '"objective":"查询交易状态","success_criteria":"取得状态",'
            '"suggested_tools":["chain_query"],"depends_on":[], '
            '"requires_confirmation":false}]}',
        ]
    )

    plan, attempts = create_plan(model, "查询交易", [_Tool()])

    assert attempts == 2
    assert plan.steps[0].suggested_tools == ["chain_query"]


def test_create_plan_falls_back_to_single_step() -> None:
    model = _StaticModel(["invalid"])

    plan, attempts = create_plan(model, "直接回答问题", [_Tool()])

    assert attempts == 2
    assert plan.goal == "直接回答问题"
    assert len(plan.steps) == 1
    assert plan.steps[0].suggested_tools == []
