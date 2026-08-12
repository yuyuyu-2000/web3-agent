"""Task-aware rolling summaries for bounded active thread context."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from chaincloud_agent_service.agent.context_builder import TokenCounter

ROLLING_SUMMARY_SYSTEM_PROMPT = """你是线程上下文压缩器。只根据提供的旧摘要和消息生成 JSON。
禁止编造，缺失字段使用空值、空数组或空对象。必须保留地址、交易哈希、时间范围、
重要数字、用户明确约束、权限、失败、未解决错误和未决问题。不要回答用户。
JSON schema:
{
  "current_goal": "",
  "confirmed_user_constraints": [],
  "important_entities": [],
  "important_numbers": [],
  "current_plan": {},
  "completed_steps": [],
  "pending_steps": [],
  "important_tool_findings": [],
  "failed_attempts": [],
  "unresolved_errors": [],
  "permissions_approvals": [],
  "clarified_state": {},
  "decisions_made": [],
  "open_questions": []
}
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def message_id(message: Any, index: int) -> str:
    existing = getattr(message, "id", None)
    if existing:
        return str(existing)
    content = str(getattr(message, "content", ""))
    kind = str(getattr(message, "type", message.__class__.__name__))
    digest = hashlib.sha256(f"{index}:{kind}:{content}".encode("utf-8")).hexdigest()[:20]
    return f"synthetic:{digest}"


def is_context_length_error(exc: Exception) -> bool:
    text = str(exc).lower()
    code = str(getattr(exc, "code", "") or "").lower()
    return any(marker in f"{code} {text}" for marker in (
        "context_length_exceeded", "maximum context length", "context window",
        "prompt is too long", "prompt too long", "too many tokens",
    ))


def reactive_compact_retry(
    *, state: dict[str, Any], manager: "RollingSummaryManager", summary_model: Any,
    build_context: Any, call: Any,
) -> tuple[Any, Any, dict[str, Any]]:
    """Run once, compact on a context error, then retry exactly once."""
    working_state = state
    context = build_context(working_state)
    compact_update: dict[str, Any] = {}
    audit = getattr(context, "audit", {})
    if int(audit.get("total_tokens", 0)) >= int(manager.max_input_tokens * 0.9):
        compact_update = manager.compact(state, summary_model, mode="proactive")
        working_state = {**state, **compact_update}
        if compact_update.get("conversation_summary"):
            context = build_context(working_state)
    try:
        return call(context), context, compact_update
    except Exception as exc:
        if not is_context_length_error(exc):
            raise
        reactive_update = manager.compact(working_state, summary_model, mode="reactive")
        if not reactive_update.get("conversation_summary"):
            raise
        shadow = {**working_state, **reactive_update}
        retry_context = build_context(shadow)
        merged_update = {**compact_update, **reactive_update}
        return call(retry_context), retry_context, merged_update


@dataclass(frozen=True)
class CompactDecision:
    should_compact: bool
    reason: str
    active_tokens: int
    trigger_tokens: int


class RollingSummaryManager:
    def __init__(
        self, *, model_name: str, max_input_tokens: int,
        trigger_ratio: float = 0.70, recent_messages: int = 12,
        reactive_recent_messages: int = 4, summary_input_tokens: int | None = None,
        max_failures: int = 3,
    ) -> None:
        self.counter = TokenCounter(model_name)
        self.max_input_tokens = max_input_tokens
        self.trigger_ratio = min(max(trigger_ratio, 0.1), 0.95)
        self.recent_messages = max(2, recent_messages)
        self.reactive_recent_messages = max(1, reactive_recent_messages)
        self.summary_input_tokens = summary_input_tokens or max(512, max_input_tokens // 2)
        self.max_failures = max(1, max_failures)

    @classmethod
    def from_settings(cls, settings: Any) -> "RollingSummaryManager":
        return cls(
            model_name=str(getattr(settings, "openai_model", "gpt-4o-mini")),
            max_input_tokens=int(getattr(settings, "max_input_tokens", 96000)),
            trigger_ratio=float(getattr(settings, "rolling_summary_trigger_ratio", 0.70)),
            recent_messages=int(getattr(settings, "rolling_summary_recent_messages", 12)),
            reactive_recent_messages=int(getattr(settings, "rolling_summary_reactive_recent_messages", 4)),
            summary_input_tokens=int(getattr(settings, "rolling_summary_max_input_tokens", 32000)),
            max_failures=int(getattr(settings, "rolling_summary_max_failures", 3)),
        )

    def active_messages(self, state: dict[str, Any]) -> list[Any]:
        messages = list(state.get("messages", []))
        start = min(max(int(state.get("summarized_until", 0)), 0), len(messages))
        return messages[start:]

    def decide(self, state: dict[str, Any], *, projected_tokens: int = 0) -> CompactDecision:
        active_tokens = self.counter.messages(self.active_messages(state))
        trigger = int(self.max_input_tokens * self.trigger_ratio)
        failures = int(state.get("compact_failure_count", 0))
        if failures >= self.max_failures:
            return CompactDecision(False, "compact_circuit_open", active_tokens, trigger)
        if active_tokens >= trigger:
            return CompactDecision(True, "active_messages_threshold", active_tokens, trigger)
        if active_tokens + max(0, projected_tokens) >= int(self.max_input_tokens * 0.9):
            return CompactDecision(True, "projected_input_near_limit", active_tokens, trigger)
        return CompactDecision(False, "below_threshold", active_tokens, trigger)

    def proactive_compact(self, state: dict[str, Any], model: Any, *, projected_tokens: int = 0) -> dict[str, Any]:
        decision = self.decide(state, projected_tokens=projected_tokens)
        if not decision.should_compact:
            return {}
        return self.compact(state, model, mode="proactive")

    def compact(self, state: dict[str, Any], model: Any, *, mode: Literal["proactive", "reactive"] = "proactive") -> dict[str, Any]:
        if int(state.get("compact_failure_count", 0)) >= self.max_failures:
            return {"compact_events": [*state.get("compact_events", []), {
                "type": "rolling_compact", "mode": mode, "status": "skipped",
                "reason": "compact_circuit_open",
            }]}
        messages = list(state.get("messages", []))
        start = min(max(int(state.get("summarized_until", 0)), 0), len(messages))
        keep = self.reactive_recent_messages if mode == "reactive" else self.recent_messages
        target = max(start, len(messages) - keep)
        step_start = state.get("step_message_start")
        if isinstance(step_start, int) and state.get("status") == "executing":
            target = min(target, max(start, step_start))
        if target <= start:
            return {"compact_events": [*state.get("compact_events", []), {
                "type": "rolling_compact", "mode": mode, "status": "skipped",
                "reason": "no_safe_message_prefix", "active_tokens_before": self.counter.messages(messages[start:]),
            }]}

        batch: list[Any] = []
        batch_tokens = 0
        for message in messages[start:target]:
            cost = self.counter.message(message)
            if batch and batch_tokens + cost > self.summary_input_tokens:
                break
            batch.append(message)
            batch_tokens += cost
        if not batch:
            batch = [messages[start]]

        prompt = self._summary_prompt(state, batch)
        before = self.counter.messages(messages[start:])
        try:
            response = model.invoke([
                SystemMessage(content=ROLLING_SUMMARY_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            summary = self._parse_summary(str(getattr(response, "content", response)))
        except Exception as exc:
            return {
                "compact_failure_count": int(state.get("compact_failure_count", 0)) + 1,
                "compact_events": [*state.get("compact_events", []), {
                    "type": "rolling_compact", "mode": mode, "status": "failed",
                    "reason": exc.__class__.__name__, "active_tokens_before": before,
                }],
            }

        new_until = start + len(batch)
        ids = [message_id(message, index) for index, message in enumerate(messages[start:new_until], start=start)]
        after_messages = messages[new_until:]
        after = self.counter.text(json.dumps(summary, ensure_ascii=False)) + self.counter.messages(after_messages)
        return {
            "conversation_summary": summary,
            "summarized_message_ids": [*state.get("summarized_message_ids", []), *ids],
            "summarized_until": new_until,
            "summary_version": int(state.get("summary_version", 0)) + 1,
            "summary_updated_at": utc_now_iso(),
            "compact_failure_count": 0,
            "compact_events": [*state.get("compact_events", []), {
                "type": "rolling_compact", "mode": mode, "status": "success",
                "reason": "context_length_error" if mode == "reactive" else "token_threshold",
                "summarized_from": start, "summarized_until": new_until,
                "summarized_count": len(batch), "active_tokens_before": before,
                "active_tokens_after": after, "summary_version": int(state.get("summary_version", 0)) + 1,
            }],
        }

    def _summary_prompt(self, state: dict[str, Any], batch: Sequence[Any]) -> str:
        transcript = "\n".join(
            f"{getattr(message, 'type', message.__class__.__name__)}: {getattr(message, 'content', '')}"
            for message in batch
        )
        payload = {
            "previous_summary": state.get("conversation_summary"),
            "agent_state": {
                "current_goal": _latest_human(list(state.get("messages", []))),
                "plan": state.get("plan"), "current_step_id": state.get("current_step_id"),
                "step_results": state.get("step_results", []),
                "status": state.get("status"), "failure_reason": state.get("failure_reason"),
                "last_tool_errors": state.get("last_tool_errors", []),
                "approved_permission_keys": state.get("approved_permission_keys", []),
                "pending_permission": state.get("pending_permission"),
                "clarified_state": state.get("clarified_state", {}),
            },
            "messages_to_compact": transcript,
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def _parse_summary(text: str) -> dict[str, Any]:
        candidate = text.strip()
        if "```" in candidate:
            candidate = candidate.replace("```json", "").replace("```", "").strip()
        start, end = candidate.find("{"), candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start:end + 1]
        value = json.loads(candidate)
        if not isinstance(value, dict):
            raise ValueError("rolling summary must be a JSON object")
        required = {
            "current_goal", "confirmed_user_constraints", "important_entities",
            "important_numbers", "current_plan", "completed_steps", "pending_steps",
            "important_tool_findings", "failed_attempts", "unresolved_errors",
            "permissions_approvals", "clarified_state", "decisions_made", "open_questions",
        }
        if not required.issubset(value):
            raise ValueError("rolling summary is missing required fields")
        return value


def _latest_human(messages: list[Any]) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) == "human":
            return str(getattr(message, "content", ""))
    return ""
