from __future__ import annotations

from copy import deepcopy

from chaincloud_agent_service.agent.evaluation.machine_validator import (
    MACHINE_STEP_VALIDATOR_VERSION,
    validate_deterministic_step,
)
from chaincloud_agent_service.agent.planning.models import PlanStep, StepResult


TXID = "a" * 64
OTHER_TXID = "b" * 64


def _step(criteria: str, *, depends_on: list[str] | None = None) -> PlanStep:
    return PlanStep(
        id="step_2",
        objective="验证交易",
        success_criteria=criteria,
        suggested_tools=["get_tron_transaction"],
        depends_on=depends_on or [],
        estimated_tool_calls=1,
    )


def _result(*, txid: str = TXID) -> StepResult:
    reference = {
        "result_id": "result-1",
        "tool_name": "get_tron_transaction",
        "tool_args": {"txid": txid},
        "created_at": "2026-01-01T00:00:00+00:00",
        "evidence_source": "onchain_rpc",
        "raw_result_location": "/tmp/result-1.json",
        "content_sha256": "c" * 64,
    }
    facts = {
        "txid": txid,
        "transaction_status": "SUCCESS",
        "receipt_status": "SUCCESS",
        "block_number": 123,
    }
    return StepResult(
        step_id="step_2",
        status="success",
        summary="complete",
        structured_facts=[facts],
        dependency_outputs={"step_2": facts},
        result_references=[reference],
        provenance=[reference],
        tool_calls=["get_tron_transaction"],
    )


def _record(*, txid: str = TXID, truncated: bool = False, ambiguity=None):
    return {
        "result_id": "result-1",
        "tool_name": "get_tron_transaction",
        "tool_args": {"txid": txid},
        "result_contract": {
            "result_kind": "canonical_transaction",
            "terminal": True,
            "structured_facts_complete": True,
            "truncated": truncated,
            "ambiguity": ambiguity or [],
            "provenance_complete": True,
        },
    }


def _validate(criteria: str, **kwargs):
    return validate_deterministic_step(
        step=kwargs.pop("step", _step(criteria)),
        result=kwargs.pop("result", _result()),
        tool_result_records=kwargs.pop("tool_result_records", [_record()]),
        prior_step_results=kwargs.pop("prior_step_results", []),
        last_tool_errors=kwargs.pop("last_tool_errors", []),
        tool_events=kwargs.pop("tool_events", []),
        **kwargs,
    )


def test_supported_criteria_pass_with_auditable_predicates() -> None:
    decision = _validate(
        "required fields: txid, block_number；交易哈希格式正确；transaction and receipt status SUCCESS"
    )
    assert decision.decision == "pass"
    assert decision.validator_version == MACHINE_STEP_VALIDATOR_VERSION
    names = {item.name for item in decision.checked_predicates}
    assert "required_field:txid" in names
    assert "identifier_hash_format" in names
    assert "transaction_status_success" in names
    assert "receipt_status_success" in names


def test_unprovable_natural_language_criteria_is_unknown() -> None:
    decision = _validate("形成可信且有洞察力的业务结论")
    assert decision.decision == "unknown"
    assert any(item.outcome == "unknown" for item in decision.checked_predicates)


def test_partially_supported_criteria_is_unknown() -> None:
    decision = _validate("交易哈希格式正确；形成可信且有洞察力的业务结论")
    assert decision.decision == "unknown"
    assert "业务结论" in decision.checked_predicates[-1].detail


def test_txid_dependency_mismatch_fails() -> None:
    prior = [{
        "step_id": "step_1",
        "structured_facts": [{"tx_hash": OTHER_TXID}],
    }]
    decision = _validate(
        "交易哈希格式正确",
        step=_step("交易哈希格式正确", depends_on=["step_1"]),
        prior_step_results=prior,
    )
    assert decision.decision == "fail"
    assert "dependency_binding" in decision.reason


def test_prefixed_tx_hash_dependency_matches_txid() -> None:
    prior = [{
        "step_id": "step_1",
        "structured_facts": [{"deposit_tx_hash": TXID}],
    }]
    decision = _validate(
        "交易哈希格式正确",
        step=_step("交易哈希格式正确", depends_on=["step_1"]),
        prior_step_results=prior,
    )
    assert decision.decision == "pass"


def test_failed_receipt_when_success_required_fails() -> None:
    result = _result()
    result.structured_facts[0]["receipt_status"] = "FAILED"
    decision = _validate("transaction and receipt status SUCCESS", result=result)
    assert decision.decision == "fail"
    assert "receipt_status_success" in decision.reason


def test_required_field_missing_fails() -> None:
    result = _result()
    result.structured_facts[0].pop("block_number")
    decision = _validate("required fields: block_number", result=result)
    assert decision.decision == "fail"
    assert "required_field:block_number" in decision.reason


def test_empty_single_row_result_fails() -> None:
    result = _result()
    result.structured_facts = [{"row_count": 0, "sample": []}]
    decision = _validate("row_count = 1；单行结果", result=result)
    assert decision.decision == "fail"
    assert "row_count_single" in decision.reason


def test_truncated_result_fails() -> None:
    decision = _validate(
        "交易哈希格式正确", tool_result_records=[_record(truncated=True)]
    )
    assert decision.decision == "fail"
    assert "not_truncated" in decision.reason


def test_ambiguous_result_fails() -> None:
    decision = _validate(
        "交易哈希格式正确",
        tool_result_records=[_record(ambiguity=["multiple matches"])],
    )
    assert decision.decision == "fail"
    assert "no_ambiguity" in decision.reason


def test_tool_args_differ_from_persisted_record_fails() -> None:
    decision = _validate(
        "交易哈希格式正确", tool_result_records=[_record(txid=OTHER_TXID)]
    )
    assert decision.decision == "fail"
    assert "tool_args_record_match" in decision.reason


def test_error_retry_and_recovery_each_fail() -> None:
    error_result = _result()
    error_result.status = "failed"
    error_result.error = "timeout"
    assert _validate("交易哈希格式正确", result=error_result).decision == "fail"
    assert _validate(
        "交易哈希格式正确", last_tool_errors=[{"error_type": "timeout"}]
    ).decision == "fail"
    assert _validate(
        "交易哈希格式正确",
        tool_events=[{"step_id": "step_2", "attempt": 2, "recovered": True}],
    ).decision == "fail"


def test_wrong_tool_name_fails() -> None:
    result = deepcopy(_result())
    result.tool_calls = ["postgres_select"]
    decision = _validate("交易哈希格式正确", result=result)
    assert decision.decision == "fail"
    assert "planned_tool_name" in decision.reason
