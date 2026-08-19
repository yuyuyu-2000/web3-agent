from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .models import EvalCase, EvalObservation


class AgentAdapter(Protocol):
    def run(
        self, case: EvalCase, *, variant: dict[str, bool] | None = None
    ) -> EvalObservation: ...


class HttpAgentAdapter:
    """Calls only the public Agent API; ground truth is never serialized."""

    def __init__(
        self, endpoint: str, *, token: str | None = None, timeout: float = 180
    ) -> None:
        self.endpoint, self.token, self.timeout = endpoint, token, timeout

    def _turn(
        self, case: EvalCase, query: str, thread_id: str, planning: str
    ) -> dict[str, Any]:
        body = json.dumps(
            {
                "thread_id": thread_id,
                "message": query,
                "planning": planning,
                "debug": True,
            }
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.endpoint, data=body, headers=headers, method="POST"
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())

    def run(
        self, case: EvalCase, *, variant: dict[str, bool] | None = None
    ) -> EvalObservation:
        unsupported = [
            name
            for name in ("error_recovery", "memory_recall", "context_compression")
            if variant and variant.get(name) is False
        ]
        if unsupported:
            raise NotImplementedError(
                f"HTTP adapter has no request-scoped switch for: {', '.join(unsupported)}"
            )
        planning = case.planning
        if variant and not variant.get("planner", True):
            planning = "direct"
        thread_id = f"eval-{case.case_id}-{time.time_ns()}"
        started = time.perf_counter()
        turns: list[dict[str, Any]] = []
        try:
            payload = self._turn(case, case.user_query, thread_id, planning)
            turns.append(payload)
            for turn in case.turns:
                payload = self._turn(case, turn.user_query, thread_id, planning)
                turns.append(payload)
            trace = dict(payload.get("execution_trace") or {})
            trace["chat_trace"] = payload.get("trace") or []
            return EvalObservation(
                case_id=case.case_id,
                reply=payload.get("reply", ""),
                status=payload.get("status"),
                failure_reason=payload.get("failure_reason"),
                execution_trace=trace,
                latency_ms=(time.perf_counter() - started) * 1000,
                turn_observations=turns[:-1],
            )
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            return EvalObservation(
                case_id=case.case_id,
                error=str(exc),
                status="failed",
                latency_ms=(time.perf_counter() - started) * 1000,
            )


class ReplayAdapter:
    """Offline adapter for CI/evaluator tests. The file contains observations, never cases."""

    def __init__(self, path: str | Path) -> None:
        self.rows = {}
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    self.rows[row["case_id"]] = row

    def run(
        self, case: EvalCase, *, variant: dict[str, bool] | None = None
    ) -> EvalObservation:
        row = self.rows.get(case.case_id)
        if row is None:
            return EvalObservation(
                case_id=case.case_id,
                status="failed",
                error="missing replay observation",
            )
        return EvalObservation.model_validate(row)
