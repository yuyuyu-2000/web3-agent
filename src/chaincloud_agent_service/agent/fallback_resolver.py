"""Deterministic fallback selection shared by validation and runtime recovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field

from chaincloud_agent_service.agent.permission import evaluate_step_permission
from chaincloud_agent_service.agent.planning.models import PlanStep


class BlockedToolUnavailable(BaseModel):
    error_type: str = "blocked_tool_unavailable"
    unavailable_tools: list[str] = Field(default_factory=list)
    declared_fallbacks: list[str] = Field(default_factory=list)
    available_fallbacks: list[str] = Field(default_factory=list)
    recoverable: bool = False
    reason: str


@dataclass(frozen=True)
class FallbackResolution:
    selected_tool: str | None
    recoverable: bool
    reason: str
    rejected: dict[str, str]


def tool_is_available(tool: Any) -> bool:
    """Honor an optional runtime availability flag; registered tools default available."""
    metadata = getattr(tool, "metadata", None)
    if isinstance(metadata, dict) and metadata.get("available") is False:
        return False
    marker = getattr(tool, "available", None)
    return marker is not False


def _schema_fields(tool: Any) -> tuple[set[str], set[str]] | None:
    schema = getattr(tool, "args_schema", None)
    fields = getattr(schema, "model_fields", None)
    if not isinstance(fields, dict):
        return None
    names = set(fields)
    required = {
        name for name, field in fields.items()
        if callable(getattr(field, "is_required", None)) and field.is_required()
    }
    return names, required


def capability_matches(primary: Any, fallback: Any) -> bool:
    """Conservatively prove call-shape compatibility without semantic guessing."""
    primary_schema = _schema_fields(primary)
    fallback_schema = _schema_fields(fallback)
    if primary_schema is None or fallback_schema is None:
        return False
    primary_names, primary_required = primary_schema
    fallback_names, fallback_required = fallback_schema
    return fallback_required <= primary_names and primary_required <= fallback_names


def resolve_fallback(
    *,
    step: PlanStep,
    original_tool: str,
    registered_tools: Mapping[str, Any],
    available_tool_names: set[str],
    approved_permission_keys: Sequence[str] | None,
    remaining_budget: int,
) -> FallbackResolution:
    """Select only a declared, available, compatible and permission-safe fallback."""
    rejected: dict[str, str] = {}
    if remaining_budget <= 0:
        return FallbackResolution(None, False, "工具调用预算已耗尽", rejected)
    primary = registered_tools.get(original_tool)
    if primary is None:
        return FallbackResolution(None, False, "主工具未注册，无法证明 fallback capability 匹配", rejected)

    for name in step.fallback_tools:
        if name == original_tool:
            rejected[name] = "same_as_failed_tool"
            continue
        fallback = registered_tools.get(name)
        if fallback is None or name not in available_tool_names:
            rejected[name] = "unavailable"
            continue
        if not capability_matches(primary, fallback):
            rejected[name] = "capability_mismatch"
            continue
        fallback_step = step.model_copy(update={"suggested_tools": [name]})
        permission = evaluate_step_permission(
            fallback_step, list(approved_permission_keys or [])
        )
        if permission.action == "DENY":
            rejected[name] = "permission_denied"
            continue
        return FallbackResolution(
            name, True, f"选择已声明且通过确定性校验的 fallback：{name}", rejected
        )
    return FallbackResolution(None, False, "没有可确定性证明安全、可用的 fallback", rejected)


def blocked_tool_unavailable(
    *,
    step: PlanStep,
    unavailable_tools: Sequence[str],
    registered_tools: Mapping[str, Any],
    available_tool_names: set[str],
    approved_permission_keys: Sequence[str] | None,
    remaining_budget: int,
) -> tuple[BlockedToolUnavailable, FallbackResolution]:
    original = str(next(iter(unavailable_tools), ""))
    resolution = resolve_fallback(
        step=step,
        original_tool=original,
        registered_tools=registered_tools,
        available_tool_names=available_tool_names,
        approved_permission_keys=approved_permission_keys,
        remaining_budget=remaining_budget,
    )
    available_fallbacks = [
        name for name in step.fallback_tools if name in available_tool_names
    ]
    blocked = BlockedToolUnavailable(
        unavailable_tools=list(unavailable_tools),
        declared_fallbacks=list(step.fallback_tools),
        available_fallbacks=available_fallbacks,
        recoverable=resolution.recoverable,
        reason=(
            f"计划工具当前不可用：{', '.join(unavailable_tools)}；"
            f"{resolution.reason}"
        ),
    )
    return blocked, resolution
