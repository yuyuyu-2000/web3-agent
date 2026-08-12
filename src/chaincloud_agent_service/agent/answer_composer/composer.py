from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from openai import OpenAIError

from chaincloud_agent_service.agent.answer_composer.complexity import (
    AnswerStyle,
    choose_answer_style,
)
from chaincloud_agent_service.agent.answer_composer.prompts import (
    DETAILED_ANSWER_COMPOSER_SYSTEM_PROMPT,
    LIGHTWEIGHT_ANSWER_COMPOSER_SYSTEM_PROMPT,
    build_answer_composer_user_prompt,
)
from chaincloud_agent_service.agent.answer_composer.renderer import (
    build_answer_context,
    build_fallback_answer,
    message_content_to_text,
)


def _last_ai_text(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai" or msg.__class__.__name__ == "AIMessage":
            if getattr(msg, "tool_calls", None):
                continue
            text = message_content_to_text(getattr(msg, "content", ""))
            if text.strip():
                return text.strip()
    return ""


def _system_prompt_for_style(style: AnswerStyle) -> str:
    if style == AnswerStyle.LIGHTWEIGHT:
        return LIGHTWEIGHT_ANSWER_COMPOSER_SYSTEM_PROMPT
    return DETAILED_ANSWER_COMPOSER_SYSTEM_PROMPT


def _build_composer_messages(messages: list[Any]) -> list[Any]:
    context = build_answer_context(messages)
    style = choose_answer_style(messages)
    return [
        SystemMessage(content=_system_prompt_for_style(style)),
        HumanMessage(content=build_answer_composer_user_prompt(context)),
    ]


def compose_final_answer(model: Any, messages: list[Any]) -> AIMessage:
    draft = _last_ai_text(messages)

    try:
        response = model.invoke(_build_composer_messages(messages))
        text = message_content_to_text(getattr(response, "content", "")).strip()
    except OpenAIError:
        text = ""
    except Exception:
        text = ""

    if not text:
        text = build_fallback_answer(draft)

    return AIMessage(content=text)


async def acompose_final_answer(
    model: Any, messages: list[Any], *, model_messages: list[Any] | None = None
) -> AIMessage:
    """Generate the final answer through the model's native async token stream."""
    draft = _last_ai_text(messages)
    parts: list[str] = []

    try:
        async for chunk in model.astream(model_messages or _build_composer_messages(messages)):
            parts.append(message_content_to_text(getattr(chunk, "content", "")))
    except OpenAIError:
        parts = []
    except Exception:
        parts = []

    text = "".join(parts).strip()
    if not text:
        text = build_fallback_answer(draft)
    return AIMessage(content=text)
