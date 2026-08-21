from __future__ import annotations


from langchain_core.tools import StructuredTool

from chaincloud_agent_service.evaluation.deterministic import evaluate_case
from chaincloud_agent_service.evaluation.faults import FaultInjectingTool
from chaincloud_agent_service.evaluation.metrics import aggregate
from chaincloud_agent_service.evaluation.models import (
    EvalCase,
    EvalObservation,
    FaultSpec,
)
from chaincloud_agent_service.evaluation.runner import load_cases
from chaincloud_agent_service.evaluation.runner import EvaluationRunner


def _case(**updates):  # type: ignore[no-untyped-def]
    raw = {
        "case_id": "c1",
        "category": "tool",
        "user_query": "query",
        "ground_truth": {"expected_result": "success", "required_facts": ["42"]},
        "expected_tools": ["lookup"],
        "expected_arguments": [
            {"tool": "lookup", "path": "limit", "op": "lte", "value": 10}
        ],
        "expected_permission": "none",
        "tags": [],
    }
    raw.update(updates)
    return EvalCase.model_validate(raw)


def test_dataset_has_thirty_valid_unique_cases() -> None:
    cases = load_cases("eval/test_cases.jsonl")
    assert len(cases) == 30
    assert len({case.case_id for case in cases}) == 30
    assert {
        "direct",
        "database",
        "tron",
        "multi_tool",
        "chart",
        "scheduler",
        "recovery",
        "memory",
        "monitor",
    }.issubset({case.category for case in cases})


def test_deterministic_evaluator_uses_trace_not_ground_truth_in_agent_input() -> None:
    case = _case()
    observation = EvalObservation(
        case_id="c1",
        reply="answer is 42",
        status="completed",
        execution_trace={
            "tool_result_records": [{"tool_name": "lookup", "tool_args": {"limit": 5}}],
            "decision_events": [],
            "request_summary": {"final_status": "success"},
        },
    )
    result = evaluate_case(case, observation)
    assert result.deterministic_passed is True
    assert all(check.passed for check in result.checks)


def test_empty_expected_tools_means_no_tool_call() -> None:
    case = _case(
        expected_tools=[],
        ground_truth={"expected_result": "success"},
        expected_arguments=[],
    )
    observation = EvalObservation(
        case_id="c1",
        status="completed",
        execution_trace={
            "tool_result_records": [{"tool_name": "lookup", "tool_args": {}}]
        },
    )
    assert evaluate_case(case, observation).deterministic_passed is False


def test_permission_none_accepts_readonly_allow_decisions() -> None:
    case = _case(expected_tools=None, expected_arguments=[])
    observation = EvalObservation(
        case_id="c1",
        reply="42",
        status="completed",
        execution_trace={
            "decision_events": [
                {"decision_type": "permission_gate", "action": "allow"},
                {"decision_type": "permission_gate", "action": "allow"},
            ]
        },
    )
    result = evaluate_case(case, observation)
    permission_check = next(c for c in result.checks if c.name == "permission_gate")
    assert permission_check.passed is True


def test_permission_none_rejects_confirmation_or_denial() -> None:
    case = _case(expected_tools=None, expected_arguments=[])
    for action in ("need_confirm", "deny"):
        observation = EvalObservation(
            case_id="c1",
            reply="42",
            status="completed",
            execution_trace={
                "decision_events": [
                    {"decision_type": "permission_gate", "action": action}
                ]
            },
        )
        permission_check = next(
            c for c in evaluate_case(case, observation).checks
            if c.name == "permission_gate"
        )
        assert permission_check.passed is False


def test_permission_not_checked_requires_no_gate_event() -> None:
    case = _case(
        expected_tools=None,
        expected_arguments=[],
        expected_permission="not_checked",
    )
    without_gate = EvalObservation(case_id="c1", reply="42", status="completed")
    assert next(
        c for c in evaluate_case(case, without_gate).checks
        if c.name == "permission_gate"
    ).passed is True

    with_gate = without_gate.model_copy(
        update={
            "execution_trace": {
                "decision_events": [
                    {"decision_type": "permission_gate", "action": "allow"}
                ]
            }
        }
    )
    assert next(
        c for c in evaluate_case(case, with_gate).checks
        if c.name == "permission_gate"
    ).passed is False


def test_fault_injection_is_repeatable_first_timeout_second_success() -> None:
    tool = StructuredTool.from_function(
        lambda value: value * 2, name="lookup", description="lookup"
    )
    wrapped = FaultInjectingTool(
        tool, [FaultSpec(tool="lookup", error="timeout", times=1)]
    )
    try:
        wrapped.invoke({"value": 2})
        assert False
    except TimeoutError:
        pass
    assert wrapped.invoke({"value": 2}) == 4


def test_metrics_use_null_for_unavailable_tokens_and_compute_percentiles() -> None:
    result = evaluate_case(
        _case(),
        EvalObservation(
            case_id="c1",
            reply="42",
            status="completed",
            latency_ms=100,
            execution_trace={
                "tool_result_records": [
                    {"tool_name": "lookup", "tool_args": {"limit": 5}}
                ],
                "request_summary": {
                    "final_status": "success",
                    "llm_calls": 2,
                    "tool_calls": 1,
                },
            },
        ),
    )
    metrics = aggregate([result])
    assert metrics["overall"]["task_success_rate"] == 1
    assert metrics["performance"]["latency_p95_ms"] == 100
    assert metrics["performance"]["avg_total_tokens"] is None


def test_metrics_ignore_legacy_redacted_token_values() -> None:
    result = evaluate_case(
        _case(),
        EvalObservation(
            case_id="c1",
            reply="42",
            status="completed",
            execution_trace={
                "tool_result_records": [
                    {"tool_name": "lookup", "tool_args": {"limit": 5}}
                ],
                "request_summary": {
                    "final_status": "success",
                    "input_tokens": "[REDACTED]",
                    "output_tokens": "[REDACTED]",
                    "total_tokens": "[REDACTED]",
                },
            },
        ),
    )
    assert aggregate([result])["performance"]["avg_total_tokens"] is None


def test_runner_skips_cases_unsupported_by_adapter(tmp_path) -> None:  # type: ignore[no-untyped-def]
    class Adapter:
        unsupported_capabilities = {"fault_injection"}

        def run(self, case, *, variant=None):  # type: ignore[no-untyped-def]
            return EvalObservation(case_id=case.case_id, status="completed")

    normal = _case(
        expected_tools=None,
        expected_arguments=[],
        ground_truth={"expected_result": "success"},
    )
    fault = normal.model_copy(
        update={"case_id": "fault", "required_capabilities": ["fault_injection"]}
    )
    payload = EvaluationRunner(Adapter()).run(
        [normal, fault], output_dir=tmp_path, run_id="skip"
    )
    assert payload["evaluated_cases"] == 1
    assert payload["skipped_cases"][0]["case_id"] == "fault"
