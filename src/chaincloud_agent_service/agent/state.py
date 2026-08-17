from __future__ import annotations

from typing import Annotated, Any, Literal

from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict


class AgentState(TypedDict):
    """Checkpointed state shared by planning and execution nodes."""

    messages: Annotated[list[Any], add_messages]
    user_id: NotRequired[str | None]
    trace_id: NotRequired[str]
    trace_thread_id: NotRequired[str]
    trace_started_at: NotRequired[str]
    trace_started_monotonic: NotRequired[float]
    node_events: NotRequired[list[dict[str, Any]]]
    tool_events: NotRequired[list[dict[str, Any]]]
    decision_events: NotRequired[list[dict[str, Any]]]
    error_events: NotRequired[list[dict[str, Any]]]
    context_events: NotRequired[list[dict[str, Any]]]
    conversation_summary: NotRequired[dict[str, Any] | None]
    active_recalled_memories: NotRequired[list[dict[str, Any]]]
    recalled_memory_keys: NotRequired[list[str]]
    memory_recall_query: NotRequired[str | None]
    memory_recall_events: NotRequired[list[dict[str, Any]]]
    summarized_message_ids: NotRequired[list[str]]
    summarized_until: NotRequired[int]
    summary_version: NotRequired[int]
    summary_updated_at: NotRequired[str | None]
    compact_failure_count: NotRequired[int]
    compact_events: NotRequired[list[dict[str, Any]]]
    request_summary: NotRequired[dict[str, Any] | None]
    requested_mode: NotRequired[Literal["auto", "direct", "planned"]]
    execution_mode: NotRequired[Literal["direct", "planned"] | None]
    route_action: NotRequired[Literal["route", "resume", "clarify", "cancel"]]
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
    clarified_state: NotRequired[dict[str, Any]]
    state_validation: NotRequired[dict[str, Any] | None]
    state_validation_action: NotRequired[Literal["VALID", "MISSING"] | None]
    block_resolution: NotRequired[Literal["clarification", "partial", "fail"] | None]
    step_results: NotRequired[list[dict[str, Any]]]
    candidate_step_result: NotRequired[dict[str, Any] | None]
    step_message_start: NotRequired[int]
    planner_attempts: NotRequired[int]
    replanning_count: NotRequired[int]
    tool_call_count: NotRequired[int]
    last_tool_errors: NotRequired[list[dict[str, Any]]]
    tool_result_records: NotRequired[list[dict[str, Any]]]
    permission_failure: NotRequired[dict[str, Any] | None]
    direct_tool_call_count: NotRequired[int]
    step_tool_call_count: NotRequired[int]
    step_tool_call_limit: NotRequired[int]
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
    reviewer_reasoning_effort: NotRequired[Literal["low", "high"] | None]
    reviewer_reasoning_reason: NotRequired[str | None]
    status: NotRequired[
        Literal[
            "planning",
            "executing",
            "completed",
            "partial",
            "degraded",
            "failed",
            "waiting_confirmation",
            "permission_denied",
            "blocked_missing_state",
        ]
    ]
    failure_reason: NotRequired[str | None]
