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
