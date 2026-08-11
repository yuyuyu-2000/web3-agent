from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EvaluationDecision(BaseModel):
    action: Literal["pass", "retry", "replan", "partial", "fail"]
    reason: str = Field(min_length=1)
    feedback: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

