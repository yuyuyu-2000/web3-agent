"""Scenario-aware LLM context construction with a shared token budget."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from chaincloud_agent_service.agent.tool_results import tool_message_for_context

ContextScene = Literal[
    "router", "planner", "direct_executor", "planned_executor",
    "answer_composer", "reviewer",
]


class ContextBudgetError(ValueError):
    """Raised when protected context alone cannot fit the configured input budget."""


class TokenCounter:
    """Count model tokens with tiktoken, falling back to a conservative estimator."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.method = "estimate"
        self._encoding: Any = None
        try:
            import tiktoken
            try:
                self._encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                self._encoding = tiktoken.get_encoding("cl100k_base")
            self.method = "tiktoken"
        except (ImportError, OSError):
            self._encoding = None

    def text(self, value: str) -> int:
        if not value:
            return 0
        if self._encoding is not None:
            return len(self._encoding.encode(value, disallowed_special=()))
        # UTF-8 bytes / 3 is deliberately conservative for mixed Chinese/English/JSON.
        return max(1, (len(value.encode("utf-8")) + 2) // 3)

    def message(self, message: BaseMessage) -> int:
        content = _content_text(getattr(message, "content", ""))
        tool_calls = getattr(message, "tool_calls", None)
        extra = json.dumps(tool_calls, ensure_ascii=False, default=str) if tool_calls else ""
        return 4 + self.text(content) + self.text(extra)

    def messages(self, messages: Sequence[BaseMessage]) -> int:
        return 2 + sum(self.message(message) for message in messages)


@dataclass
class ContextPart:
    category: str
    messages: list[BaseMessage]
    priority: int
    protected: bool = False
    newest_first: bool = False


@dataclass
class ContextBuildResult:
    scene: ContextScene
    messages: list[BaseMessage]
    audit: dict[str, Any]


@dataclass
class ContextBuilder:
    model: str
    model_context_window: int = 128_000
    max_input_tokens: int = 96_000
    reserved_output_tokens: int = 8_000
    counter: TokenCounter = field(init=False)

    def __post_init__(self) -> None:
        if self.model_context_window <= 0 or self.max_input_tokens <= 0:
            raise ValueError("context token limits must be positive")
        if self.reserved_output_tokens < 0:
            raise ValueError("reserved_output_tokens must not be negative")
        hard_input_limit = self.model_context_window - self.reserved_output_tokens
        if hard_input_limit <= 0:
            raise ValueError("reserved output must be smaller than model context window")
        self.max_input_tokens = min(self.max_input_tokens, hard_input_limit)
        self.counter = TokenCounter(self.model)

    @classmethod
    def from_settings(cls, settings: Any) -> "ContextBuilder":
        return cls(
            model=str(getattr(settings, "openai_model", "gpt-4o-mini")),
            model_context_window=int(getattr(settings, "model_context_window", 128_000)),
            max_input_tokens=int(getattr(settings, "max_input_tokens", 96_000)),
            reserved_output_tokens=int(getattr(settings, "reserved_output_tokens", 8_000)),
        )

    def build(self, scene: ContextScene, parts: Sequence[ContextPart]) -> ContextBuildResult:
        protected = [part for part in parts if part.protected]
        optional = sorted((part for part in parts if not part.protected), key=lambda p: p.priority)
        protected_tokens = sum(self.counter.messages(part.messages) for part in protected)
        if protected_tokens > self.max_input_tokens:
            raise ContextBudgetError(
                f"protected context for {scene} requires {protected_tokens} tokens, "
                f"input budget is {self.max_input_tokens}"
            )

        kept: dict[int, list[BaseMessage]] = {id(part): list(part.messages) for part in protected}
        remaining = self.max_input_tokens - protected_tokens
        trimmed: list[dict[str, Any]] = []
        category_tokens: dict[str, int] = {}
        for part in protected:
            category_tokens[part.category] = category_tokens.get(part.category, 0) + self.counter.messages(part.messages)

        # Allocate high-value optional context first. Lower-priority parts are omitted first.
        for part in optional:
            selected: list[BaseMessage] = []
            groups = _atomic_message_groups(part.messages)
            candidates = list(reversed(groups)) if part.newest_first else groups
            for group in candidates:
                cost = sum(self.counter.message(message) for message in group)
                if cost <= remaining:
                    if part.newest_first:
                        selected[0:0] = group
                    else:
                        selected.extend(group)
                    remaining -= cost
                else:
                    trimmed.append({
                        "category": part.category,
                        "reason": "token_budget_exceeded",
                        "tokens": cost,
                    })
            kept[id(part)] = selected
            category_tokens[part.category] = category_tokens.get(part.category, 0) + sum(
                self.counter.message(message) for message in selected
            )

        output: list[BaseMessage] = []
        for part in parts:
            output.extend(kept.get(id(part), []))
        total = self.counter.messages(output)
        audit = {
            "type": "context_build",
            "scene": scene,
            "model": self.model,
            "token_counter": self.counter.method,
            "model_context_window": self.model_context_window,
            "max_input_tokens": self.max_input_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "total_tokens": total,
            "category_tokens": category_tokens,
            "trimmed": trimmed,
            "remaining_input_tokens": max(0, self.max_input_tokens - total),
        }
        return ContextBuildResult(scene=scene, messages=output, audit=audit)

    @staticmethod
    def summary_part(conversation_summary: dict[str, Any] | None) -> ContextPart:
        text = (
            "当前线程较早历史的 task-aware rolling summary：\n"
            + json.dumps(conversation_summary, ensure_ascii=False, default=str)
            if conversation_summary else ""
        )
        return _text_part("summary", SystemMessage, text, 8)

    @staticmethod
    def summary_constraints_part(conversation_summary: dict[str, Any] | None) -> ContextPart:
        if not conversation_summary:
            return ContextPart("summary_constraints", [], 5, True)
        keys = (
            "current_goal", "confirmed_user_constraints", "important_entities",
            "important_numbers", "unresolved_errors", "permissions_approvals",
            "clarified_state", "open_questions",
        )
        critical = {key: conversation_summary.get(key) for key in keys}
        return _text_part(
            "summary_constraints", SystemMessage,
            "线程摘要中的关键目标、约束与未决状态：\n"
            + json.dumps(critical, ensure_ascii=False, default=str),
            5, True,
        )

    def router(self, *, system_prompt: str, current_request: str, history: Sequence[Any], tool_names: str, conversation_summary: dict[str, Any] | None = None) -> ContextBuildResult:
        recent = _recent_non_tool(history, exclude_latest=True, limit=8)
        return self.build("router", [
            _text_part("system", SystemMessage, system_prompt, 1, True),
            self.summary_constraints_part(conversation_summary),
            self.summary_part(conversation_summary),
            ContextPart("recent_history", recent, 7, newest_first=True),
            _text_part("current_request", HumanMessage, f"用户当前请求：\n{current_request}\n\n可用工具名称：{tool_names}", 2, True),
        ])

    def planner(self, *, system_prompt: str, current_request: str, history: Sequence[Any], tool_catalog: str, feedback: str = "", conversation_summary: dict[str, Any] | None = None) -> ContextBuildResult:
        recent = _recent_non_tool(history, exclude_latest=True, limit=8)
        request = f"用户当前目标：\n{current_request}\n\n可用工具：\n{tool_catalog}{feedback}"
        return self.build("planner", [
            _text_part("system", SystemMessage, system_prompt, 1, True),
            self.summary_constraints_part(conversation_summary),
            self.summary_part(conversation_summary),
            ContextPart("recent_history", recent, 7, newest_first=True),
            _text_part("current_request", HumanMessage, request, 2, True),
        ])

    def executor(self, *, scene: Literal["direct_executor", "planned_executor"], system_prompt: str, current_request: str, critical_state: str, messages: Sequence[Any], dependency_evidence: Sequence[BaseMessage] = (), conversation_summary: dict[str, Any] | None = None) -> ContextBuildResult:
        memory, history = _split_memory_and_history(messages)
        evidence_ids = {id(message) for message in dependency_evidence}
        history = [message for message in history if id(message) not in evidence_ids]
        latest_human_index = max(
            (index for index, message in enumerate(history) if isinstance(message, HumanMessage)),
            default=-1,
        )
        history = [
            tool_message_for_context(
                message,
                compact_old=(scene == "planned_executor" or index < latest_human_index),
            ) if isinstance(message, ToolMessage) else message
            for index, message in enumerate(history)
        ]
        recent = _recent_messages(history, current_request=current_request, limit=12)
        return self.build(scene, [
            _text_part("system", SystemMessage, system_prompt, 1, True),
            ContextPart("memory", memory, 6),
            self.summary_constraints_part(conversation_summary),
            self.summary_part(conversation_summary),
            ContextPart("recent_history", recent, 7, newest_first=True),
            _text_part("current_request", HumanMessage, current_request, 2, True),
            _text_part("critical_state", SystemMessage, critical_state, 3, True),
            ContextPart("dependency_evidence", list(dependency_evidence), 4, True),
        ])

    def answer_composer(self, *, system_prompt: str, current_request: str, execution_summary: str, evidence: Sequence[BaseMessage], draft: str, memory: Sequence[BaseMessage] = (), conversation_summary: dict[str, Any] | None = None) -> ContextBuildResult:
        evidence_messages = [
            HumanMessage(content=f"工具证据 {getattr(message, 'name', 'unknown_tool')}：\n{_content_text(tool_message_for_context(message, compact_old=True).content)}")
            for message in evidence
        ]
        return self.build("answer_composer", [
            _text_part("system", SystemMessage, system_prompt, 1, True),
            ContextPart("memory", list(memory), 6),
            self.summary_constraints_part(conversation_summary),
            self.summary_part(conversation_summary),
            _text_part("current_request", HumanMessage, current_request, 2, True),
            _text_part("critical_state", SystemMessage, execution_summary, 3, True),
            ContextPart("evidence", evidence_messages, 4, True),
            _text_part("draft", AIMessage, draft, 7),
        ])

    def reviewer(self, *, system_prompt: str, current_request: str, execution_summary: str, answer: str) -> ContextBuildResult:
        payload = json.dumps({"user_request": current_request, "answer_draft": answer, "execution_summary": execution_summary}, ensure_ascii=False)
        return self.build("reviewer", [
            _text_part("system", SystemMessage, system_prompt, 1, True),
            _text_part("current_request", HumanMessage, current_request, 2, True),
            _text_part("critical_state", HumanMessage, payload, 3, True),
        ])


def _text_part(category: str, message_type: type[BaseMessage], text: str, priority: int, protected: bool = False) -> ContextPart:
    return ContextPart(category, [message_type(content=text)] if text.strip() else [], priority, protected)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content)
    return str(content or "")


def _recent_non_tool(messages: Sequence[Any], *, exclude_latest: bool, limit: int) -> list[BaseMessage]:
    source = list(messages[:-1] if exclude_latest else messages)
    return [message for message in source if not isinstance(message, ToolMessage) and getattr(message, "type", None) != "tool"][-limit:]


def _recent_messages(messages: Sequence[Any], *, current_request: str, limit: int) -> list[BaseMessage]:
    source: list[BaseMessage] = []
    skipped_current = False
    for message in reversed(messages):
        if not skipped_current and isinstance(message, HumanMessage) and _content_text(message.content).strip() == current_request.strip():
            skipped_current = True
            continue
        source.append(message)
    source.reverse()

    # Select whole assistant-tool exchanges. Slicing individual messages can leave
    # a ToolMessage without the assistant tool_calls that authorizes it, which the
    # OpenAI API rejects with HTTP 400.
    result: list[BaseMessage] = []
    for group in reversed(_atomic_message_groups(source)):
        if _is_orphan_tool_group(group):
            continue
        if result and len(result) + len(group) > limit:
            break
        result[0:0] = group
        if len(result) >= limit:
            break
    return result


def _split_memory_and_history(messages: Sequence[Any]) -> tuple[list[BaseMessage], list[BaseMessage]]:
    memory: list[BaseMessage] = []
    history: list[BaseMessage] = []
    for message in messages:
        if isinstance(message, SystemMessage) and "长期记忆" in _content_text(message.content):
            memory.append(message)
        else:
            history.append(message)
    return memory, history


def _atomic_message_groups(messages: Sequence[BaseMessage]) -> list[list[BaseMessage]]:
    """Keep an assistant tool request and its ToolMessages in one budget unit."""
    groups: list[list[BaseMessage]] = []
    index = 0
    source = list(messages)
    while index < len(source):
        message = source[index]
        calls = list(getattr(message, "tool_calls", None) or [])
        if calls:
            call_ids = {
                str(call.get("id") if isinstance(call, dict) else getattr(call, "id", ""))
                for call in calls
            }
            group = [message]
            cursor = index + 1
            while cursor < len(source):
                candidate = source[cursor]
                if not isinstance(candidate, ToolMessage):
                    break
                if str(getattr(candidate, "tool_call_id", "")) not in call_ids:
                    break
                group.append(candidate)
                cursor += 1
            groups.append(group)
            index = cursor
            continue
        groups.append([message])
        index += 1
    return groups


def _is_orphan_tool_group(group: Sequence[BaseMessage]) -> bool:
    """Return whether a group starts with a tool result lacking its request."""
    return bool(group) and isinstance(group[0], ToolMessage)
