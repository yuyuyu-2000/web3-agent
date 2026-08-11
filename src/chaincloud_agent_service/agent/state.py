from __future__ import annotations

from typing import Annotated, Any, Literal

from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict


class AgentState(TypedDict):
    """Checkpointed state shared by planning and execution nodes."""

    messages: Annotated[list[Any], add_messages]
    requested_mode: NotRequired[Literal["auto", "direct", "planned"]]
    execution_mode: NotRequired[Literal["direct", "planned"] | None]
    route_action: NotRequired[Literal["route", "resume", "cancel"]]
    route_reason: NotRequired[str | None]
    route_confidence: NotRequired[float | None]
    route_source: NotRequired[
        Literal["api_override", "rule", "model", "fallback", "resume"] | None
    ]
    route_signals: NotRequired[list[str]]
    plan: NotRequired[dict[str, Any] | None]
    current_step_id: NotRequired[str | None]
    approved_step_ids: NotRequired[list[str]]
    approved_permission_keys: NotRequired[list[str]]
    pending_permission: NotRequired[dict[str, Any] | None]
    permission_action: NotRequired[Literal["ALLOW", "NEED_CONFIRM", "DENY"] | None]
    step_results: NotRequired[list[dict[str, Any]]]
    candidate_step_result: NotRequired[dict[str, Any] | None]
    step_message_start: NotRequired[int]
    planner_attempts: NotRequired[int]
    replanning_count: NotRequired[int]
    tool_call_count: NotRequired[int]
    direct_tool_call_count: NotRequired[int]
    step_tool_call_count: NotRequired[int]
    step_retry_count: NotRequired[int]
    evaluation_action: NotRequired[
        Literal["pass", "retry", "replan", "partial", "fail"] | None
    ]
    evaluation_feedback: NotRequired[str | None]
    review_required: NotRequired[bool]
    review_reason: NotRequired[str | None]
    review_action: NotRequired[Literal["approve", "revise"] | None]
    review_feedback: NotRequired[str | None]
    review_attempts: NotRequired[int]
    status: NotRequired[
        Literal[
            "planning",
            "executing",
            "completed",
            "partial",
            "failed",
            "waiting_confirmation",
            "permission_denied",
        ]
    ]
    failure_reason: NotRequired[str | None]
