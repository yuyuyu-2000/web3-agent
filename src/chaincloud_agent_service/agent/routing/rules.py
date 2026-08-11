from __future__ import annotations

import re

from chaincloud_agent_service.agent.routing.models import RouteDecision


_PLANNING_PHRASES = (
    "制定计划",
    "先规划",
    "分步骤",
    "逐步",
    "完整调查",
    "深入调查",
    "全面调查",
    "完整报告",
    "风险报告",
    "攻击复盘",
    "资金流追踪",
    "交叉核验",
    "交叉验证",
)

_MULTI_SOURCE_PHRASES = (
    "多个数据源",
    "多数据源",
    "公开资料",
    "公司数据库",
    "链上 rpc",
    "链上数据",
    "新闻",
    "社交媒体",
)

_COMPLEX_DELIVERABLES = (
    "生成图表",
    "生成报告",
    "风险排名",
    "证据链",
    "完整分析",
    "详细分析",
)

_SIDE_EFFECT_PHRASES = (
    "创建定时",
    "设置定时",
    "创建任务",
    "设置告警",
    "每天执行",
    "定期执行",
    "schedule",
)

_DIRECT_PHRASES = (
    "一句话",
    "简单回答",
    "简要回答",
    "简单解释",
    "快速查",
    "只告诉我",
    "不用展开",
    "是什么",
    "什么意思",
)

_SINGLE_LOOKUP_PHRASES = (
    "交易状态",
    "交易是否成功",
    "地址余额",
    "当前余额",
    "区块高度",
    "当前价格",
    "当前 tvl",
    "查一下",
    "查询一下",
)

_SEQUENCE_RE = re.compile(r"(?:先|首先).{0,80}(?:然后|再|接着|最后)", re.DOTALL)
_MULTI_ACTION_RE = re.compile(
    r"(?:查询|查找|获取|追踪|识别|比较|核验|分析|生成|总结).{0,60}"
    r"(?:并且|并|然后|再|最后|同时).{0,60}"
    r"(?:查询|查找|获取|追踪|识别|比较|核验|分析|生成|总结)",
    re.DOTALL,
)


def _hits(text: str, phrases: tuple[str, ...]) -> list[str]:
    return [phrase for phrase in phrases if phrase in text]


def route_by_rules(message: str) -> RouteDecision | None:
    """Return only high-confidence decisions; ambiguous requests go to the model."""

    text = message.strip().lower()
    if not text:
        return RouteDecision(
            mode="direct",
            reason="空请求不需要任务规划",
            confidence=1.0,
            signals=["empty_request"],
            source="rule",
        )

    planning_hits = _hits(text, _PLANNING_PHRASES)
    side_effect_hits = _hits(text, _SIDE_EFFECT_PHRASES)
    deliverable_hits = _hits(text, _COMPLEX_DELIVERABLES)
    source_hits = _hits(text, _MULTI_SOURCE_PHRASES)
    has_sequence = bool(_SEQUENCE_RE.search(text))
    has_multiple_actions = bool(_MULTI_ACTION_RE.search(text))

    planned_score = 0
    signals: list[str] = []
    if planning_hits:
        planned_score += 4
        signals.append("explicit_planning")
    if side_effect_hits:
        planned_score += 4
        signals.append("side_effect")
    if has_sequence:
        planned_score += 4
        signals.append("dependent_sequence")
    if has_multiple_actions:
        planned_score += 3
        signals.append("multiple_actions")
    if len(source_hits) >= 2:
        planned_score += 3
        signals.append("multiple_data_sources")
    if deliverable_hits:
        planned_score += 2
        signals.append("complex_deliverable")

    if planned_score >= 4:
        return RouteDecision(
            mode="planned",
            reason="请求包含多阶段、跨数据源或需确认的复杂执行信号",
            confidence=min(0.99, 0.72 + planned_score * 0.03),
            signals=signals,
            source="rule",
        )

    direct_hits = _hits(text, _DIRECT_PHRASES)
    lookup_hits = _hits(text, _SINGLE_LOOKUP_PHRASES)
    direct_score = 0
    direct_signals: list[str] = []
    if direct_hits:
        direct_score += 2
        direct_signals.append("simple_answer_requested")
    if lookup_hits:
        direct_score += 2
        direct_signals.append("single_lookup")
    if len(text) <= 12 and text.endswith(("是什么", "什么意思", "？", "?")):
        direct_score += 2
        direct_signals.append("short_question")

    if direct_score >= 3 and planned_score == 0:
        return RouteDecision(
            mode="direct",
            reason="请求是单项查询或简短回答，不需要任务拆解",
            confidence=min(0.96, 0.76 + direct_score * 0.04),
            signals=direct_signals,
            source="rule",
        )
    return None

