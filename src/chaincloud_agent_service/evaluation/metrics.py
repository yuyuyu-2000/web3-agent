from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean
from typing import Any

from .models import CaseResult


def _pct(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lo, hi = math.floor(rank), math.ceil(rank)
    return (
        ordered[lo]
        if lo == hi
        else ordered[lo] + (ordered[hi] - ordered[lo]) * (rank - lo)
    )


def _check_rate(results: list[CaseResult], prefix: str) -> float | None:
    checks = [
        c
        for r in results
        for c in r.checks
        if c.name.startswith(prefix) and c.passed is not None
    ]
    return mean(float(c.passed) for c in checks) if checks else None


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    summaries = [
        r.observation.execution_trace.get("request_summary", {}) for r in results
    ]
    latencies = [
        float(
            r.observation.latency_ms
            if r.observation.latency_ms is not None
            else s.get("total_duration_ms")
        )
        for r, s in zip(results, summaries)
        if r.observation.latency_ms is not None
        or s.get("total_duration_ms") is not None
    ]

    def avg(field: str) -> float | None:
        values: list[float] = []
        for summary in summaries:
            value = summary.get(field)
            if value is None or isinstance(value, bool):
                continue
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                # A malformed or legacy/redacted trace must not abort the run.
                continue
        return mean(values) if values else None

    def overall_for(group: list[CaseResult]) -> dict[str, Any]:
        rates = {
            name: sum(r.outcome == name for r in group) / len(group) if group else 0.0
            for name in ("success", "partial", "degraded", "failed")
        }
        return {
            "cases": len(group),
            "task_success_rate": rates["success"],
            "tool_selection_accuracy": _check_rate(group, "tool_selection"),
            "tool_argument_accuracy": _check_rate(group, "argument:"),
            "recovery_success_rate": _check_rate(group, "recovery"),
            "permission_gate_accuracy": _check_rate(group, "permission_gate"),
            **{f"{k}_rate": v for k, v in rates.items() if k != "success"},
        }

    overall = overall_for(results)
    nodes: dict[str, list[float]] = defaultdict(list)
    for r in results:
        for event in r.observation.execution_trace.get("node_events", []):
            if event.get("duration_ms") is not None:
                nodes[str(event.get("node_name"))].append(float(event["duration_ms"]))
    by_category = {
        category: overall_for(group)
        for category in sorted({r.category for r in results})
        if (group := [r for r in results if r.category == category])
    }
    machine_decisions = [
        event
        for result in results
        for event in result.observation.execution_trace.get("decision_events", [])
        if event.get("decision_type") == "machine_step_validator"
    ]
    llm_actions = {
        (result.case_id, str(event.get("step_id"))): str(event.get("action"))
        for result in results
        for event in result.observation.execution_trace.get("decision_events", [])
        if event.get("decision_type") == "evaluator"
    }
    machine_rows = [
        (result.case_id, event)
        for result in results
        for event in result.observation.execution_trace.get("decision_events", [])
        if event.get("decision_type") == "machine_step_validator"
    ]
    result_kinds = sorted({str(event.get("result_kind") or "unknown") for event in machine_decisions})
    conflicts_by_kind = {}
    for kind in result_kinds:
        rows = [(case_id, event) for case_id, event in machine_rows if str(event.get("result_kind") or "unknown") == kind]
        passes = [(case_id, event) for case_id, event in rows if event.get("action") == "pass"]
        conflicts = sum(
            llm_actions.get((case_id, str(event.get("step_id")))) not in {None, "pass"}
            for case_id, event in passes
        )
        conflicts_by_kind[kind] = {
            "machine_passes": len(passes),
            "machine_pass_llm_non_pass_conflicts": conflicts,
            "conflict_rate": conflicts / len(passes) if passes else None,
        }
    return {
        "overall": overall,
        "performance": {
            "latency_p50_ms": _pct(latencies, 0.50),
            "latency_p95_ms": _pct(latencies, 0.95),
            "avg_llm_calls": avg("llm_calls"),
            "avg_tool_calls": avg("tool_calls"),
            "avg_input_tokens": avg("input_tokens"),
            "avg_output_tokens": avg("output_tokens"),
            "avg_total_tokens": avg("total_tokens"),
            "avg_tool_retries": avg("tool_retries"),
            "avg_step_retries": avg("step_retries"),
            "avg_replans": avg("replans"),
            "nodes": {
                name: {"avg_ms": mean(v), "p95_ms": _pct(v, 0.95)}
                for name, v in sorted(nodes.items())
            },
        },
        "memory": {
            "retrieval_hit_rate": _check_rate(results, "memory_hit"),
            "retrieval_accuracy": _check_rate(results, "memory_accuracy"),
        },
        "machine_step_validator": {
            "decisions": len(machine_decisions),
            "pass": sum(event.get("action") == "pass" for event in machine_decisions),
            "fail": sum(event.get("action") == "fail" for event in machine_decisions),
            "unknown": sum(event.get("action") == "unknown" for event in machine_decisions),
            "machine_pass_llm_non_pass_conflicts": sum(
                llm_actions.get((case_id, str(event.get("step_id")))) not in {None, "pass"}
                for case_id, event in machine_rows
                if event.get("action") == "pass"
            ),
            "by_result_kind": conflicts_by_kind,
        },
        "by_category": by_category,
    }
