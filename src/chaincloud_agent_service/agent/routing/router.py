from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from chaincloud_agent_service.agent.routing.models import (
    RequestedPlanningMode,
    RouteDecision,
)
from chaincloud_agent_service.agent.routing.rules import route_by_rules


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

ROUTER_SYSTEM_PROMPT = """你是任务执行路由器，只判断请求应直接执行还是先制定计划。
只输出 JSON：
{"mode":"direct或planned","reason":"简短原因","confidence":0到1,"signals":["信号"]}

选择 direct：无需工具或单个工具大概率完成；没有步骤依赖；不要求跨数据源核验；不涉及副作用。
选择 planned：需要多个工具或数据源；存在先后依赖；需要调查、证据链、报告、图表、比较、风险评估；或涉及创建/修改外部状态。
不要回答用户问题，不要制定计划，不要输出思维过程。
"""


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return str(content)


def _parse_decision(text: str) -> RouteDecision:
    fenced = _JSON_FENCE_RE.search(text)
    candidate = fenced.group(1).strip() if fenced else text.strip()
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    decision = RouteDecision.model_validate(json.loads(candidate))
    decision.source = "model"
    return decision


def _tool_names(tools: list[Any]) -> str:
    names = [str(getattr(tool, "name", tool.__class__.__name__)) for tool in tools]
    return ", ".join(names) if names else "无"


def decide_route(
    model: Any,
    message: str,
    tools: list[Any],
    requested_mode: RequestedPlanningMode = "auto",
    conversation_context: str = "",
) -> RouteDecision:
    if requested_mode in {"direct", "planned"}:
        return RouteDecision(
            mode=requested_mode,
            reason=f"调用方明确指定 {requested_mode} 模式",
            confidence=1.0,
            signals=["api_override"],
            source="api_override",
        )

    rule_decision = route_by_rules(message)
    if rule_decision is not None:
        return rule_decision

    prompt = (
        f"用户当前请求：\n{message}\n\n"
        f"近期对话背景：\n{conversation_context or '无'}\n\n"
        f"可用工具名称：{_tool_names(tools)}"
    )
    try:
        response = model.invoke(
            [
                SystemMessage(content=ROUTER_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        decision = _parse_decision(_message_text(response))
        if decision.confidence < 0.6:
            return RouteDecision(
                mode="planned",
                reason="路由模型置信度不足，保守使用规划模式",
                confidence=decision.confidence,
                signals=[*decision.signals, "low_confidence"],
                source="fallback",
            )
        return decision
    except Exception:
        return RouteDecision(
            mode="planned",
            reason="路由判断失败，保守使用规划模式",
            confidence=0.0,
            signals=["router_error"],
            source="fallback",
        )

