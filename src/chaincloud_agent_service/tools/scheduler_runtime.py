"""Background scheduler runtime for scheduled prompts."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

TaskExecutor = Callable[[str, str], Awaitable[str]]

_scheduler = BackgroundScheduler()
_executor: TaskExecutor | None = None
_lock = threading.Lock()


def _task_file() -> Path:
    raw = os.environ.get("SCHEDULER_TASKS_FILE", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("tasks.json").resolve()


def _result_file() -> Path:
    raw = os.environ.get("SCHEDULER_RESULTS_FILE", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("scheduled_results.jsonl").resolve()


def _load_tasks() -> list[dict[str, Any]]:
    path = _task_file()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def _save_tasks(tasks: list[dict[str, Any]]) -> None:
    path = _task_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def _append_result(task_id: str, prompt: str, success: bool, output: str) -> None:
    path = _result_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task_id,
        "prompt": prompt,
        "success": success,
        "output": output,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _remove_task_if_date(task_id: str) -> None:
    with _lock:
        tasks = _load_tasks()
        filtered = [t for t in tasks if t.get("id") != task_id]
        if len(filtered) != len(tasks):
            _save_tasks(filtered)


def _execute_task(prompt: str, task_id: str, trigger_type: str) -> None:
    executor = _executor
    if executor is None:
        _append_result(task_id, prompt, False, "scheduler executor not initialized")
        if trigger_type == "date":
            _remove_task_if_date(task_id)
        return
    try:
        output = asyncio.run(executor(prompt, task_id))
        _append_result(task_id, prompt, True, output)
    except Exception as e:
        _append_result(task_id, prompt, False, str(e))
    finally:
        if trigger_type == "date":
            _remove_task_if_date(task_id)


def _restore_tasks() -> None:
    tasks = _load_tasks()
    now = datetime.now(timezone.utc)
    alive: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("id", "")).strip()
        prompt = str(task.get("prompt", "")).strip()
        trigger_type = str(task.get("trigger_type", "")).strip()
        if not task_id or not prompt or trigger_type not in {"date", "cron"}:
            continue
        try:
            if trigger_type == "date":
                run_date_raw = str(task.get("run_date", "")).strip()
                run_date = datetime.fromisoformat(run_date_raw)
                if run_date.tzinfo is None:
                    run_date = run_date.replace(tzinfo=timezone.utc)
                if run_date <= now:
                    continue
                _scheduler.add_job(
                    _execute_task,
                    trigger=DateTrigger(run_date=run_date),
                    args=[prompt, task_id, "date"],
                    id=task_id,
                    replace_existing=True,
                )
                alive.append(task)
            else:
                cron_kwargs = task.get("cron_kwargs")
                if not isinstance(cron_kwargs, dict) or not cron_kwargs:
                    continue
                _scheduler.add_job(
                    _execute_task,
                    trigger=CronTrigger(**cron_kwargs),
                    args=[prompt, task_id, "cron"],
                    id=task_id,
                    replace_existing=True,
                )
                alive.append(task)
        except Exception:
            continue
    with _lock:
        _save_tasks(alive)


def start_scheduler(executor: TaskExecutor) -> None:
    global _executor
    _executor = executor
    if _scheduler.running:
        return
    _restore_tasks()
    _scheduler.start()


def add_monitor_scan_job(scan: Callable[[], Any], interval_sec: int) -> None:
    """Register exactly one coalesced monitor job for all users and rules."""
    _scheduler.add_job(
        scan,
        trigger="interval",
        seconds=max(5, interval_sec),
        id="chaincloud-monitor-scan",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=max(10, interval_sec),
    )


def add_scheduled_task(
    prompt: str,
    trigger_type: str,
    run_date: str | None = None,
    cron_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import uuid

    prompt = (prompt or "").strip()
    trigger_type = (trigger_type or "").strip().lower()
    if not prompt:
        return {"error": "prompt 不能为空"}
    if trigger_type not in {"date", "cron"}:
        return {"error": "trigger_type 必须是 date 或 cron"}
    if not _scheduler.running:
        return {"error": "scheduler 未启动"}

    task_id = str(uuid.uuid4())
    record: dict[str, Any] = {
        "id": task_id,
        "prompt": prompt,
        "trigger_type": trigger_type,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        if trigger_type == "date":
            if not run_date:
                return {"error": "date 任务必须提供 run_date (ISO 8601)"}
            dt = datetime.fromisoformat(run_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt <= datetime.now(timezone.utc):
                return {"error": "run_date 必须是未来时间"}
            _scheduler.add_job(
                _execute_task,
                trigger=DateTrigger(run_date=dt),
                args=[prompt, task_id, "date"],
                id=task_id,
                replace_existing=True,
            )
            record["run_date"] = dt.isoformat()
        else:
            if not isinstance(cron_kwargs, dict) or not cron_kwargs:
                return {"error": "cron 任务必须提供 cron_kwargs"}
            _scheduler.add_job(
                _execute_task,
                trigger=CronTrigger(**cron_kwargs),
                args=[prompt, task_id, "cron"],
                id=task_id,
                replace_existing=True,
            )
            record["cron_kwargs"] = cron_kwargs
    except Exception as e:
        return {"error": str(e)}

    with _lock:
        tasks = _load_tasks()
        tasks.append(record)
        _save_tasks(tasks)
    return {"status": "success", "task_id": task_id}
