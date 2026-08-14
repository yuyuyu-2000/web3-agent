from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from chaincloud_agent_service.agent.review.models import ReviewDecision
from chaincloud_agent_service.agent.rolling_summary import is_context_length_error


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

_HIGH_RISK_HINTS = (
    "攻击",
    "黑客",
    "漏洞",
    "被盗",
    "洗钱",
    "制裁",
    "风险",
    "清算",
    "投资",
    "收益",
    "财务",
    "资金流",
    "安全",
    "合约权限",
)

REVIEWER_SYSTEM_PROMPT = """你是最终答案 Reviewer。审查草稿是否忠实于执行证据并完整回应用户。
只输出 JSON：
{"action":"approve或revise","reason":"原因","feedback":"具体修订要求","confidence":0到1}

重点检查：
1. 数字、地址、时间和来源是否与上下文一致。
2. 是否把推断、公开资料或模型判断误写成链上确认事实。
3. 是否遗漏用户明确要求或重要限制。
4. 是否包含上下文没有支持的结论。
没有实质问题时 approve；发现实质问题时 revise，并给出可执行的修订要求。
不要亲自重写答案，不要输出思维过程。
"""

LOW_REASONING_REVIEWER_SYSTEM_PROMPT = """你是最终答案 Reviewer。对普通 Planned 任务做快速、严格的事实核对。
只输出 JSON：
{"action":"approve或revise","reason":"原因","feedback":"具体修订要求","confidence":0到1}

只检查：
1. 最终回答是否与 executor/tool 结果一致。
2. 是否存在明显幻觉。
3. 是否遗漏用户核心问题。
4. 是否在证据不足时给出强结论。
没有实质问题时 approve；有问题时 revise。不要扩展分析，不要亲自重写答案。
"""

_HIGH_REASONING_HINTS = (
    "归因", "根因", "冲突", "矛盾", "异常", "风险", "清算", "攻击", "漏洞",
    "安全", "投资", "财务", "决策", "预测", "推断", "因果", "置信度",
)


def planned_review_effort(state: dict[str, Any], user_message: str) -> tuple[str, str]:
    """Select reviewer effort without adding another latency-producing model call."""
    text = " ".join(
        [
            user_message,
            str(state.get("evaluation_feedback") or ""),
            str(state.get("failure_reason") or ""),
        ]
    ).lower()
    reasons: list[str] = []
    if any(hint in text for hint in _HIGH_REASONING_HINTS):
        reasons.append("复杂推理或风险判断")
    route_confidence = state.get("route_confidence")
    if route_confidence is not None and float(route_confidence) < 0.75:
        reasons.append("路由置信度较低")
    if int(state.get("replanning_count") or 0) > 0:
        reasons.append("发生重新规划")
    evaluator_actions = {
        event.get("action")
        for event in state.get("decision_events", [])
        if event.get("decision_type") == "evaluator"
    }
    had_tool_error = bool(state.get("last_tool_errors")) or any(
        event.get("status") == "error" for event in state.get("tool_events", [])
    )
    if (
        int(state.get("step_retry_count") or 0) > 0
        or evaluator_actions.intersection({"retry", "replan", "partial", "fail"})
        or had_tool_error
    ):
        reasons.append("执行存在重试或工具异常")
    if state.get("status") in {"partial", "degraded", "failed"}:
        reasons.append("执行结果不完整")
    plan = state.get("plan") or {}
    if len(plan.get("steps", [])) >= 4:
        reasons.append("计划步骤较多")
    if reasons:
        return "high", "；".join(reasons)
    return "low", "普通 Planned 结果一致性检查"


def direct_requires_review(
    user_message: str,
    route_signals: list[str],
    tool_call_count: int,
) -> tuple[bool, str]:
    text = user_message.lower()
    risk_hits = [hint for hint in _HIGH_RISK_HINTS if hint in text]
    if risk_hits:
        return True, f"Direct 请求包含高风险主题：{', '.join(risk_hits[:3])}"
    if tool_call_count >= 2:
        return True, "Direct 执行使用了多个工具结果，需要一致性审查"
    if any(signal in {"multiple_actions", "multiple_data_sources"} for signal in route_signals):
        return True, "Direct 路由包含复杂执行信号"
    return False, "简单低风险 Direct 回答无需 Reviewer"


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def _parse_decision(text: str) -> ReviewDecision:
    fenced = _JSON_FENCE_RE.search(text)
    candidate = fenced.group(1).strip() if fenced else text.strip()
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    return ReviewDecision.model_validate(json.loads(candidate))


def review_answer(
    model: Any,
    user_message: str,
    answer: str,
    execution_summary: str,
    model_messages: list[Any] | None = None,
) -> ReviewDecision:
    prompt = json.dumps(
        {
            "user_request": user_message,
            "answer_draft": answer,
            "execution_summary": execution_summary,
        },
        ensure_ascii=False,
    )
    try:
        response = model.invoke(
            model_messages or [
                SystemMessage(content=REVIEWER_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        return _parse_decision(_message_text(response))
    except Exception as exc:
        if is_context_length_error(exc):
            raise
        return ReviewDecision(
            action="approve",
            reason="Reviewer 不可用，保留 Answer Composer 输出",
            feedback="",
            confidence=0.0,
        )
