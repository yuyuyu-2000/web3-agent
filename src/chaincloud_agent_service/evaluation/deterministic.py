from __future__ import annotations

import json
import re
from typing import Any

from .models import (
    ArgumentConstraint,
    CaseResult,
    CheckResult,
    EvalCase,
    EvalObservation,
)


def _tool_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    records = trace.get("tool_result_records") or []
    calls = [
        {"name": r.get("tool_name"), "args": r.get("tool_args") or {}} for r in records
    ]
    if calls:
        return calls
    # Compatibility with API chat trace or hand-authored replay fixtures.
    for event in trace.get("trace", []) or trace.get("chat_trace", []):
        if event.get("type") != "tool_call_request":
            continue
        raw = event.get("args") or event.get("args_preview") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = {"_preview": raw}
        calls.append({"name": event.get("tool"), "args": raw})
    return calls


def _path(value: Any, dotted: str) -> tuple[bool, Any]:
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _constraint_ok(call: dict[str, Any], rule: ArgumentConstraint) -> bool:
    exists, actual = _path(call.get("args", {}), rule.path)
    if rule.op == "exists":
        return exists == bool(rule.value if rule.value is not None else True)
    if not exists:
        return False
    if rule.op == "eq":
        return actual == rule.value
    if rule.op == "contains":
        return str(rule.value).casefold() in str(actual).casefold()
    if rule.op == "regex":
        return re.search(str(rule.value), str(actual)) is not None
    if rule.op == "in":
        return actual in rule.value
    if rule.op == "gte":
        return actual >= rule.value
    if rule.op == "lte":
        return actual <= rule.value
    return False


def evaluate_case(case: EvalCase, observation: EvalObservation) -> CaseResult:
    trace = observation.execution_trace or {}
    summary = trace.get("request_summary") or {}
    calls = _tool_calls(trace)
    names = [str(c.get("name")) for c in calls]
    checks: list[CheckResult] = []
    if case.expected_tools is not None:
        expected = set(case.expected_tools)
        selection_ok = not names if not expected else expected.issubset(set(names))
        checks.append(
            CheckResult(
                name="tool_selection",
                passed=selection_ok,
                detail=f"expected={sorted(expected)} actual={names}",
            )
        )
    for rule in case.expected_arguments:
        matching = [call for call in calls if call.get("name") == rule.tool]
        checks.append(
            CheckResult(
                name=f"argument:{rule.tool}:{rule.path}",
                passed=any(_constraint_ok(call, rule) for call in matching),
                detail=f"op={rule.op} expected={rule.value!r}",
            )
        )
    permission_actions = [
        str(e.get("action", "")).lower()
        for e in trace.get("decision_events", [])
        if e.get("decision_type") == "permission_gate"
    ]
    if case.expected_permission is not None:
        expected_permission = case.expected_permission
        if expected_permission == "none":
            permission_ok = not any(
                action in {"need_confirm", "need-confirm", "deny"}
                for action in permission_actions
            )
        elif expected_permission == "not_checked":
            permission_ok = not permission_actions
        else:
            aliases = {
                "need_confirm": {"need_confirm", "need-confirm"},
                "allow": {"allow"},
                "deny": {"deny"},
            }
            permission_ok = any(
                action in aliases[expected_permission] for action in permission_actions
            )
        checks.append(
            CheckResult(
                name="permission_gate",
                passed=permission_ok,
                detail=f"expected={expected_permission} actual={permission_actions}",
            )
        )
    if case.expected_memory_keys is not None:
        events = trace.get("memory_recall_events", [])
        selected = {
            str(key)
            for event in events
            for key in (event.get("selected_memory_keys") or [])
        }
        expected_memory = set(case.expected_memory_keys)
        checks.append(
            CheckResult(
                name="memory_hit",
                passed=bool(selected) if expected_memory else not selected,
                detail=f"selected={sorted(selected)}",
            )
        )
        checks.append(
            CheckResult(
                name="memory_accuracy",
                passed=expected_memory.issubset(selected),
                detail=f"expected={sorted(expected_memory)} selected={sorted(selected)}",
            )
        )
    forbidden = set(case.ground_truth.forbidden_tools)
    if forbidden:
        checks.append(
            CheckResult(
                name="forbidden_tools",
                passed=forbidden.isdisjoint(names),
                detail=f"forbidden={sorted(forbidden)} actual={names}",
            )
        )
    answer = (
        observation.reply
        + "\n"
        + json.dumps(observation.response_metadata, ensure_ascii=False, default=str)
    ).casefold()
    for fact in case.ground_truth.required_facts:
        checks.append(
            CheckResult(name=f"required_fact:{fact}", passed=fact.casefold() in answer)
        )
    for turn in case.turns:
        for fact in turn.required_facts:
            checks.append(
                CheckResult(
                    name=f"required_fact:{fact}", passed=fact.casefold() in answer
                )
            )
    for fact in case.ground_truth.forbidden_facts:
        checks.append(
            CheckResult(
                name=f"forbidden_fact:{fact}", passed=fact.casefold() not in answer
            )
        )
    expected = case.ground_truth.expected_result
    actual_status = (
        observation.status
        or summary.get("final_status")
        or ("failed" if observation.error else None)
    )
    accepted = {
        "success": {"success", "completed"},
        "partial": {"partial"},
        "degraded": {"degraded", "waiting_confirmation", "blocked_missing_state"},
        "failed": {"failed", "permission_denied"},
        "permission_required": {
            "waiting_confirmation",
            "permission_required",
            "degraded",
        },
    }[expected]
    checks.append(
        CheckResult(
            name="task_completion",
            passed=actual_status in accepted,
            detail=f"expected={expected} actual={actual_status}",
        )
    )
    recovered = any(e.get("recovered") for e in trace.get("tool_events", []))
    if case.fault_injection and any(
        f.error in {"timeout", "429"} for f in case.fault_injection
    ):
        checks.append(
            CheckResult(
                name="recovery",
                passed=recovered and actual_status in {"success", "completed"},
                detail=f"recovered={recovered}",
            )
        )
    passed = all(c.passed is not False for c in checks)
    outcome = (
        "success"
        if passed
        else (actual_status if actual_status in {"partial", "degraded"} else "failed")
    )
    return CaseResult(
        case_id=case.case_id,
        category=case.category,
        outcome=outcome,
        deterministic_passed=passed,
        checks=checks,
        observation=observation,
        human_review_required=case.ground_truth.human_review,
    )
