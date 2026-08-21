from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ArgumentConstraint(BaseModel):
    tool: str
    path: str
    op: Literal["eq", "contains", "regex", "in", "gte", "lte", "exists"] = "eq"
    value: Any = None


class GroundTruth(BaseModel):
    expected_result: Literal[
        "success", "partial", "degraded", "failed", "permission_required"
    ]
    required_facts: list[str] = Field(default_factory=list)
    forbidden_facts: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    human_review: bool = False
    judge: bool = False


class EvalTurn(BaseModel):
    user_query: str
    required_facts: list[str] = Field(default_factory=list)


class FaultSpec(BaseModel):
    tool: str
    occurrence: int = Field(default=1, ge=1)
    error: Literal[
        "timeout", "429", "argument_error", "permission_denied", "fallback_failure"
    ]
    times: int = Field(default=1, ge=1)


class EvalCase(BaseModel):
    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    user_query: str = Field(min_length=1)
    ground_truth: GroundTruth
    expected_tools: list[str] | None = None
    expected_arguments: list[ArgumentConstraint] = Field(default_factory=list)
    expected_permission: Literal[
        "allow", "need_confirm", "deny", "none", "not_checked"
    ] | None = None
    expected_memory_keys: list[str] | None = None
    tags: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    turns: list[EvalTurn] = Field(default_factory=list)
    fault_injection: list[FaultSpec] = Field(default_factory=list)
    planning: Literal["auto", "direct", "planned"] = "auto"

    @model_validator(mode="after")
    def validate_safety_review(self) -> "EvalCase":
        if "safety-critical" in self.tags and not self.ground_truth.human_review:
            raise ValueError("safety-critical cases must enable human_review")
        return self


class EvalObservation(BaseModel):
    case_id: str
    reply: str = ""
    status: str | None = None
    failure_reason: str | None = None
    execution_trace: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None
    error: str | None = None
    turn_observations: list[dict[str, Any]] = Field(default_factory=list)
    response_metadata: dict[str, Any] = Field(default_factory=dict)


class CheckResult(BaseModel):
    name: str
    passed: bool | None
    detail: str = ""


class CaseResult(BaseModel):
    case_id: str
    category: str
    outcome: Literal["success", "partial", "degraded", "failed"]
    deterministic_passed: bool
    checks: list[CheckResult]
    observation: EvalObservation
    judge: dict[str, Any] | None = None
    human_review_required: bool = False
