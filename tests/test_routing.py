from __future__ import annotations

from langchain_core.messages import AIMessage

from chaincloud_agent_service.agent.routing.router import decide_route
from chaincloud_agent_service.agent.routing.rules import route_by_rules


class _StaticModel:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def invoke(self, messages):  # type: ignore[no-untyped-def]
        self.calls += 1
        return AIMessage(content=self.response)


def test_rule_routes_single_lookup_direct() -> None:
    decision = route_by_rules("请简单回答这笔交易的交易状态")

    assert decision is not None
    assert decision.mode == "direct"
    assert decision.source == "rule"


def test_rule_routes_dependent_sequence_planned() -> None:
    decision = route_by_rules(
        "先查询地址最近 30 天的资金流，然后识别交易对手，最后生成风险报告"
    )

    assert decision is not None
    assert decision.mode == "planned"
    assert "dependent_sequence" in decision.signals


def test_rule_leaves_ambiguous_request_for_model() -> None:
    assert route_by_rules("帮我认真看看这个地址") is None


def test_api_override_skips_model() -> None:
    model = _StaticModel("invalid")

    decision = decide_route(model, "复杂请求", [], requested_mode="direct")

    assert decision.mode == "direct"
    assert decision.source == "api_override"
    assert model.calls == 0


def test_high_confidence_rule_skips_model() -> None:
    model = _StaticModel("invalid")

    decision = decide_route(model, "请简单回答这笔交易的交易状态", [])

    assert decision.mode == "direct"
    assert decision.source == "rule"
    assert model.calls == 0


def test_ambiguous_request_uses_model_router() -> None:
    model = _StaticModel(
        '{"mode":"planned","reason":"需要调查",'
        '"confidence":0.9,"signals":["risk_investigation"]}'
    )

    decision = decide_route(model, "帮我认真看看这个地址", [])

    assert decision.mode == "planned"
    assert decision.source == "model"
    assert model.calls == 1


def test_low_confidence_model_result_falls_back_to_planned() -> None:
    model = _StaticModel(
        '{"mode":"direct","reason":"不确定",'
        '"confidence":0.3,"signals":[]}'
    )

    decision = decide_route(model, "帮我看看", [])

    assert decision.mode == "planned"
    assert decision.source == "fallback"


def test_invalid_model_result_falls_back_to_planned() -> None:
    decision = decide_route(_StaticModel("invalid"), "帮我看看", [])

    assert decision.mode == "planned"
    assert decision.source == "fallback"
