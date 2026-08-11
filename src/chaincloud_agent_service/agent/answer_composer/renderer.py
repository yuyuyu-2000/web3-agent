from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from chaincloud_agent_service.agent.answer_composer.evidence import (
    classify_tool_evidence_level,
    evidence_level_label,
)


def message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def _compact_text(text: str, max_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[:max_chars] + "...[truncated]"


def _format_tool_content(content: Any, max_chars: int) -> str:
    if isinstance(content, str):
        raw = content
    else:
        try:
            raw = json.dumps(content, ensure_ascii=False, default=str)
        except TypeError:
            raw = str(content)
    return _compact_text(raw, max_chars)


def build_answer_context(
    messages: list[Any], max_tool_chars: int = 1600, max_draft_chars: int = 3000
) -> str:
    user_questions: list[str] = []
    tool_results: list[str] = []
    draft_answers: list[str] = []

    for msg in messages:
        content = message_content_to_text(getattr(msg, "content", ""))

        if isinstance(msg, HumanMessage) or getattr(msg, "type", None) == "human":
            if content.strip():
                user_questions.append(_compact_text(content, 1200))
            continue

        if (
            isinstance(msg, ToolMessage)
            or getattr(msg, "type", None) == "tool"
            or msg.__class__.__name__ == "ToolMessage"
        ):
            tool_name = getattr(msg, "name", None) or "unknown_tool"
            level = classify_tool_evidence_level(str(tool_name))
            label = evidence_level_label(level)
            formatted = _format_tool_content(content, max_tool_chars)
            tool_results.append(
                f"- 工具: {tool_name}\n  证据等级: {label}\n  结果预览: {formatted}"
            )
            continue

        if isinstance(msg, AIMessage) or getattr(msg, "type", None) == "ai":
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                continue
            if content.strip():
                draft_answers.append(_compact_text(content, max_draft_chars))

    latest_question = user_questions[-1] if user_questions else "未识别到用户问题"
    latest_draft = draft_answers[-1] if draft_answers else "暂无原始草稿"
    tool_block = "\n".join(tool_results) if tool_results else "本轮没有工具结果。"

    return "\n\n".join(
        [
            "## 用户问题",
            latest_question,
            "## 工具结果与证据等级",
            tool_block,
            "## 原始回答草稿",
            latest_draft,
        ]
    )


def build_fallback_answer(draft: str) -> str:
    text = draft.strip() or "当前没有足够信息生成回答。"
    if "## 核心结论" in text:
        return text
    return "\n\n".join(
        [
            "## 核心结论",
            "1. 当前回答已生成，但未能完成 Answer Composer 二次编排，以下保留原始回答内容。",
            "## 详细分析",
            text,
            "## 风险与待验证点",
            "- 请结合工具 trace 或数据库/RPC 结果进一步核验关键结论。",
        ]
    )
