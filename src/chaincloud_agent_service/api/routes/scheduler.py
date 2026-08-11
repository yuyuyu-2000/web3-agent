"""Scheduler HTTP API: direct task creation without model tool-calling."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from chaincloud_agent_service.tools.scheduler_runtime import add_scheduled_task

router = APIRouter()


class ScheduleRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Scheduled prompt content")
    trigger_type: str = Field(..., pattern="^(date|cron)$", description="date or cron")
    run_date: str | None = Field(
        default=None, description="ISO 8601 datetime for date trigger"
    )
    cron_kwargs: dict[str, Any] | None = Field(
        default=None,
        description="Cron kwargs for APScheduler, e.g. {'hour': 8, 'minute': 0}",
    )


@router.post("/schedule")
async def schedule_task(body: ScheduleRequest) -> dict[str, Any]:
    return add_scheduled_task(
        prompt=body.prompt,
        trigger_type=body.trigger_type,
        run_date=body.run_date,
        cron_kwargs=body.cron_kwargs,
    )
