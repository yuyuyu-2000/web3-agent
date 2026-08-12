from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    success_criteria: str = Field(min_length=1)
    suggested_tools: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    critical: bool = True
    fallback_tools: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    goal: str = Field(min_length=1)
    steps: list[PlanStep] = Field(min_length=1, max_length=6)


class StepResult(BaseModel):
    step_id: str
    status: Literal["success", "partial", "failed"]
    summary: str
    evidence: list[str] = Field(default_factory=list)
    tool_calls: list[str] = Field(default_factory=list)
    error: str | None = None
