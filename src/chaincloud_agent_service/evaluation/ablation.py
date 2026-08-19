from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import EvalCase
from .runner import EvaluationRunner

DEFAULT_VARIANTS = {
    "baseline": {
        "planner": True,
        "error_recovery": True,
        "memory_recall": True,
        "context_compression": True,
    },
    "planner_off": {
        "planner": False,
        "error_recovery": True,
        "memory_recall": True,
        "context_compression": True,
    },
    "recovery_off": {
        "planner": True,
        "error_recovery": False,
        "memory_recall": True,
        "context_compression": True,
    },
    "memory_off": {
        "planner": True,
        "error_recovery": True,
        "memory_recall": False,
        "context_compression": True,
    },
    "compression_off": {
        "planner": True,
        "error_recovery": True,
        "memory_recall": True,
        "context_compression": False,
    },
}


def run_ablation(
    runner: EvaluationRunner,
    cases: list[EvalCase],
    output_dir: str | Path,
    variants: dict[str, dict[str, bool]] | None = None,
) -> dict[str, Any]:
    rows = {}
    for name, flags in (variants or DEFAULT_VARIANTS).items():
        try:
            result = runner.run(
                cases, output_dir=output_dir, run_id=f"ablation_{name}", variant=flags
            )
        except NotImplementedError as exc:
            rows[name] = {"status": "not_supported", "reason": str(exc)}
            continue
        overall, perf = result["metrics"]["overall"], result["metrics"]["performance"]
        rows[name] = {
            "status": "completed",
            "task_success_rate": overall["task_success_rate"],
            "latency_p50_ms": perf["latency_p50_ms"],
            "latency_p95_ms": perf["latency_p95_ms"],
            "avg_llm_calls": perf["avg_llm_calls"],
            "avg_tool_calls": perf["avg_tool_calls"],
            "avg_total_tokens": perf["avg_total_tokens"],
        }
    return rows
