"""Deterministic pre-execution state validation for planned steps."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

from chaincloud_agent_service.agent.planning.models import PlanStep


ValidationAction = Literal["VALID", "MISSING"]
BlockResolution = Literal["clarification", "partial", "fail"]

_IDENTIFIER_RE = re.compile(r"(?:0x[a-fA-F0-9]{40,64}|T[1-9A-HJ-NP-Za-km-z]{33})")
_SCHEDULE_RE = re.compile(
    r"(?:每天|每日|每周|每月|定时|cron|\d{1,2}[:：]\d{2}|"
    r"\d{4}-\d{1,2}-\d{1,2}[T\s]\d{1,2})",
    re.IGNORECASE,
)
_DEICTIC_TARGET_RE = re.compile(r"(?:这个|该|此)(?:地址|合约|交易|账户)")


@dataclass(frozen=True)
class MissingState:
    field: str
    reason: str
    question: str
    expected_format: str


@dataclass(frozen=True)
class StateValidationDecision:
    action: ValidationAction
    step_id: str
    resolution: BlockResolution | None
    missing_state: list[MissingState]
    reason: str

    def model_dump(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def validate_step_state(
    step: PlanStep,
    *,
    conversation_text: str,
    dependency_results: list[dict[str, Any]],
    clarified_state: dict[str, Any] | None,
    available_tool_names: set[str],
) -> StateValidationDecision:
    """Validate state required before Executor without consulting an LLM."""
    unavailable = [name for name in step.suggested_tools if name not in available_tool_names]
    if unavailable:
        return StateValidationDecision(
            "MISSING", step.id, "fail", [],
            f"计划引用了当前不可用的工具：{', '.join(unavailable)}",
        )

    clarified = clarified_state or {}
    context = "\n".join(
        [
            conversation_text,
            step.objective,
            step.success_criteria,
            json.dumps(dependency_results, ensure_ascii=False, default=str),
            json.dumps(clarified, ensure_ascii=False, default=str),
        ]
    )
    missing: list[MissingState] = []

    schedule_value = str(clarified.get("schedule_spec", ""))
    if "add_scheduled_task" in step.suggested_tools and not (
        _SCHEDULE_RE.search(schedule_value) or _SCHEDULE_RE.search(context)
    ):
        missing.append(
            MissingState(
                "schedule_spec",
                "创建定时任务前必须明确执行时间或周期",
                "请补充任务何时执行，例如“每天 08:00”或具体 ISO 时间。",
                "自然语言周期、HH:MM 或 ISO 8601 时间",
            )
        )

    needs_target = bool(_DEICTIC_TARGET_RE.search(f"{step.objective} {step.success_criteria}"))
    target_value = str(clarified.get("target_identifier", ""))
    free_text_value = str(clarified.get("free_text", ""))
    if needs_target and not (
        _IDENTIFIER_RE.search(target_value)
        or _IDENTIFIER_RE.search(free_text_value)
        or _IDENTIFIER_RE.search(context)
    ):
        missing.append(
            MissingState(
                "target_identifier",
                "步骤引用了目标对象，但上下文中没有可识别的地址或交易哈希",
                "请提供目标地址、合约地址或交易哈希。",
                "0x 地址/交易哈希或 Tron T 地址",
            )
        )

    if missing:
        return StateValidationDecision(
            "MISSING", step.id, "clarification", missing,
            "执行当前步骤所需的状态不完整",
        )
    return StateValidationDecision("VALID", step.id, None, [], "执行状态完整")
