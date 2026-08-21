from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage

from chaincloud_agent_service.agent.planning.models import Plan, PlanStep
from chaincloud_agent_service.agent.planning.planner import PLANNER_SYSTEM_PROMPT, create_plan
from chaincloud_agent_service.agent.schema_context import build_planner_trusted_schema_facts
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


def test_planner_prompt_uses_flexible_four_call_default() -> None:
    assert "默认应能在最多 4 次工具调用内完成" in PLANNER_SYSTEM_PROMPT
    assert "budget_reason" in PLANNER_SYSTEM_PROMPT
    assert "不要机械地把每次工具调用拆成单独步骤" in PLANNER_SYSTEM_PROMPT


def test_planner_prompt_requires_minimal_plan_and_recovery_only_discovery() -> None:
    assert "Minimal Sufficient Plan" in PLANNER_SYSTEM_PROMPT
    assert "正常路径不得加入 postgres_list_tables" in PLANNER_SYSTEM_PROMPT
    assert "schema discovery 是 recovery" in PLANNER_SYSTEM_PROMPT
    assert "确认最大值是否并列" in PLANNER_SYSTEM_PROMPT
    assert "自动调用 Answer Composer" in PLANNER_SYSTEM_PROMPT
    assert "不要为“汇总结果”" in PLANNER_SYSTEM_PROMPT


def test_planner_trusted_schema_facts_are_compact_and_share_schema_source() -> None:
    settings = SimpleNamespace(agent_database_schema_path="config/agent_database_schema.md")

    facts = build_planner_trusted_schema_facts(settings)  # type: ignore[arg-type]

    assert "schema_source=config/agent_database_schema.md" in facts
    assert "JustLend,justlend->public.justlend" in facts
    assert "croas_chain,cross_chain->public.croas_chain" in facts
    assert "known_columns=day,occurred,ingested_at" in facts
    assert "tx_hash" in facts
    assert "amount_usd" in facts
    assert "deposit_tx_hash" in facts
    assert "SELECT" not in facts
    assert "重要限制" not in facts
    assert len(facts) < 1500


def test_planner_trusted_schema_facts_degrade_when_source_is_unavailable() -> None:
    settings = SimpleNamespace(agent_database_schema_path=None)

    facts = build_planner_trusted_schema_facts(settings)  # type: ignore[arg-type]

    assert "无已加载" in facts
    assert "表或字段未知时才规划 schema discovery" in facts
