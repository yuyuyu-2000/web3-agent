from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ExecutionMode = Literal["direct", "planned"]
RequestedPlanningMode = Literal["auto", "direct", "planned"]
RouteSource = Literal["api_override", "rule", "model", "fallback", "resume"]


class RouteDecision(BaseModel):
    mode: ExecutionMode
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)
    source: RouteSource = "model"

