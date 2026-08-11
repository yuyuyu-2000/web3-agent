from __future__ import annotations

from enum import Enum
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage

from chaincloud_agent_service.agent.answer_composer.renderer import (
    message_content_to_text,
)


class AnswerStyle(str, Enum):
    LIGHTWEIGHT = "lightweight"
    DETAILED = "detailed"


_SIMPLE_HINTS = (
    "简洁",
    "简单",
    "简要",
    "概述",
    "介绍一下",
    "有哪些",
    "列一下",
    "快速说明",
    "一句话",
    "不用展开",
)

_DETAILED_HINTS = (
    "分析",
    "影响",
    "风险",
    "资金流",
    "攻击",
    "黑客",
    "漏洞",
    "利用",
    "追踪",
    "核验",
    "验证",
    "证据",
    "区分",
    "公开资料",
    "公司数据库",
    "链上",
    "rpc",
    "模型推测",
    "待验证",
    "defi",
    "aave",
    "kelpdao",
    "rseth",
    "compound",
    "euler",
    "lazarus",
)


def _latest_user_question(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) or getattr(msg, "type", None) == "human":
            text = message_content_to_text(getattr(msg, "content", ""))
            if text.strip():
                return text.strip()
    return ""


def _tool_result_count(messages: list[Any]) -> int:
    count = 0
    for msg in messages:
        if (
            isinstance(msg, ToolMessage)
            or getattr(msg, "type", None) == "tool"
            or msg.__class__.__name__ == "ToolMessage"
        ):
            count += 1
    return count


def choose_answer_style(messages: list[Any]) -> AnswerStyle:
    """Choose whether the final answer should be lightweight or detailed."""

    question = _latest_user_question(messages)
    normalized = question.lower()
    tool_count = _tool_result_count(messages)

    has_detailed_hint = any(hint.lower() in normalized for hint in _DETAILED_HINTS)
    has_simple_hint = any(hint.lower() in normalized for hint in _SIMPLE_HINTS)

    if has_detailed_hint:
        return AnswerStyle.DETAILED

    if tool_count >= 2 and not has_simple_hint:
        return AnswerStyle.DETAILED

    return AnswerStyle.LIGHTWEIGHT
