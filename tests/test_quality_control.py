from __future__ import annotations

from langchain_core.messages import AIMessage

from chaincloud_agent_service.agent.evaluation import evaluate_step
from chaincloud_agent_service.agent.planning.models import PlanStep, StepResult
from chaincloud_agent_service.agent.review import direct_requires_review, review_answer


class _StaticModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def invoke(self, messages):  # type: ignore[no-untyped-def]
        self.calls += 1
        return AIMessage(content=self.response)


def _step() -> PlanStep:
    return PlanStep(
        id="step_1",
        objective="查询交易状态",
        success_criteria="取得成功状态和区块高度",
    )


def _result() -> StepResult:
    return StepResult(
        step_id="step_1",
        status="success",
        summary="交易成功，区块高度为 100。",
        evidence=["status=success, block=100"],
    )


def test_evaluator_passes_result_that_meets_criteria() -> None:
    model = _StaticModel(
        '{"action":"pass","reason":"结果满足成功标准",'
        '"feedback":"","confidence":0.95}'
    )

    decision = evaluate_step(model, _step(), _result())

    assert decision.action == "pass"
    assert model.calls == 1


def test_evaluator_can_request_retry() -> None:
    model = _StaticModel(
        '{"action":"retry","reason":"缺少区块高度",'
        '"feedback":"补充查询区块高度","confidence":0.9}'
    )

    decision = evaluate_step(model, _step(), _result())

    assert decision.action == "retry"
    assert "区块高度" in decision.feedback


def test_evaluator_falls_back_to_previous_nonempty_behavior() -> None:
    decision = evaluate_step(_StaticModel("invalid"), _step(), _result())

    assert decision.action == "pass"
    assert decision.confidence == 0.0


def test_simple_direct_answer_skips_reviewer() -> None:
    required, _ = direct_requires_review("BTC 是什么？", [], 0)

    assert required is False


def test_high_risk_direct_answer_requires_reviewer() -> None:
    required, reason = direct_requires_review("查询这笔交易的清算风险", [], 1)

    assert required is True
    assert "高风险" in reason


def test_multi_tool_direct_answer_requires_reviewer() -> None:
    required, _ = direct_requires_review("查询这个地址", [], 2)

    assert required is True


def test_reviewer_can_request_revision() -> None:
    model = _StaticModel(
        '{"action":"revise","reason":"结论缺少证据限定",'
        '"feedback":"将未经验证的结论标为推断","confidence":0.92}'
    )

    decision = review_answer(model, "分析风险", "该地址有风险", "没有链上证据")

    assert decision.action == "revise"
    assert "推断" in decision.feedback


def test_reviewer_failure_keeps_composer_answer() -> None:
    decision = review_answer(_StaticModel("invalid"), "普通问题", "回答", "摘要")

    assert decision.action == "approve"
    assert decision.confidence == 0.0
