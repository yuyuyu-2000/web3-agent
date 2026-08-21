from __future__ import annotations

import json

from chaincloud_agent_service.agent.planning.models import PlanStep
from chaincloud_agent_service.agent.step_result_fast_path import build_deterministic_step_result
from chaincloud_agent_service.agent.tool_results import FileToolResultStore, process_tool_result


def _message(tmp_path, tool_name: str, raw: str):
    message, metadata = process_tool_result(
        store=FileToolResultStore(tmp_path), tool_name=tool_name,
        tool_args={"sql": "SELECT value FROM facts LIMIT 1"}, raw_content=raw,
        threshold_bytes=10_000, preview_chars=100,
    )
    message.tool_call_id = "call-1"
    return message, metadata


def _step(*, estimated_tool_calls: int = 1) -> PlanStep:
    return PlanStep(
        id="step_1", objective="取得完整结果", success_criteria="结果完整且可追溯",
        suggested_tools=["contract_capable_tool"], estimated_tool_calls=estimated_tool_calls,
    )


def _tron_raw() -> str:
    return json.dumps({
        "provider": "tron_public_node", "txid": "a" * 64,
        "transaction": {
            "txID": "a" * 64, "ret": [{"contractRet": "SUCCESS"}],
            "raw_data": {"contract": [{"type": "TransferContract", "parameter": {
                "value": {"owner_address": "41" + "1" * 40}
            }}]},
        },
        "transaction_info": {
            "id": "a" * 64, "blockNumber": 123, "blockTimeStamp": 456,
            "receipt": {"result": "SUCCESS"}, "log": [], "internal_transactions": [],
        },
    })


def test_canonical_terminal_contract_hits_fast_path(tmp_path) -> None:
    message, metadata = _message(tmp_path, "get_tron_transaction", _tron_raw())
    decision = build_deterministic_step_result(
        step=_step(), step_messages=[message], last_tool_errors=[], step_tool_call_count=1,
    )
    assert metadata["result_contract"]["deterministic_step_result_eligible"] is True
    assert decision.hit is True
    assert decision.result is not None
    assert decision.result.structured_facts == [metadata["structured_facts"]]
    assert decision.result.dependency_outputs["step_1"] == metadata["structured_facts"]
    assert decision.result.provenance == decision.result.result_references


def test_single_postgres_row_hits_fast_path(tmp_path) -> None:
    message, metadata = _message(
        tmp_path, "postgres_select", json.dumps([{"tx_hash": "a" * 64, "value": 42}])
    )
    decision = build_deterministic_step_result(
        step=_step(), step_messages=[message], last_tool_errors=[], step_tool_call_count=1,
    )
    assert metadata["result_contract"]["result_kind"] == "tabular_query"
    assert metadata["result_contract"]["structured_facts_complete"] is True
    assert decision.hit is True


def test_multi_row_postgres_result_rejects_without_data_loss(tmp_path) -> None:
    message, metadata = _message(
        tmp_path, "postgres_select", json.dumps([{"id": 1}, {"id": 2}])
    )
    decision = build_deterministic_step_result(
        step=_step(), step_messages=[message], last_tool_errors=[], step_tool_call_count=1,
    )
    assert metadata["result_contract"]["structured_facts_complete"] is False
    assert decision.hit is False
    assert decision.reason == "incomplete_structured_facts"


def test_error_retry_and_additional_tool_plan_reject_fast_path(tmp_path) -> None:
    message, _ = _message(tmp_path, "get_tron_transaction", _tron_raw())
    error_decision = build_deterministic_step_result(
        step=_step(), step_messages=[message], last_tool_errors=[{"error_type": "timeout"}],
        step_tool_call_count=1,
    )
    retry_decision = build_deterministic_step_result(
        step=_step(), step_messages=[message], last_tool_errors=[], step_tool_call_count=2,
    )
    additional_tool_decision = build_deterministic_step_result(
        step=_step(estimated_tool_calls=2), step_messages=[message], last_tool_errors=[],
        step_tool_call_count=1,
    )
    assert error_decision.reason == "unresolved_tool_error"
    assert retry_decision.reason == "requires_single_tool_call"
    assert additional_tool_decision.reason == "plan_may_require_additional_tool"


def test_unknown_contract_capability_rejects(tmp_path) -> None:
    message, metadata = _message(tmp_path, "generate_report", '{"status":"success"}')
    decision = build_deterministic_step_result(
        step=_step(), step_messages=[message], last_tool_errors=[], step_tool_call_count=1,
    )
    assert metadata["result_contract"]["result_kind"] == "unknown"
    assert decision.hit is False
