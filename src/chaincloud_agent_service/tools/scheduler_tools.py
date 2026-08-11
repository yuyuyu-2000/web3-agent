"""Structured tool wrapper for scheduler runtime."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool

from chaincloud_agent_service.tools.scheduler_runtime import add_scheduled_task


def make_scheduler_tool() -> StructuredTool:
    def _invoke(
        prompt: str,
        trigger_type: str,
        run_date: str | None = None,
        cron_kwargs: dict[str, Any] | None = None,
    ) -> str:
        result = add_scheduled_task(
            prompt=prompt,
            trigger_type=trigger_type,
            run_date=run_date,
            cron_kwargs=cron_kwargs,
        )
        return json.dumps(result, ensure_ascii=False)

    return StructuredTool.from_function(
        name="add_scheduled_task",
        description=(
            "添加定时任务。trigger_type='date' 时传 run_date(ISO 8601)；"
            "trigger_type='cron' 时传 cron_kwargs，如 {'hour': 8, 'minute': 0}。"
        ),
        func=_invoke,
    )
