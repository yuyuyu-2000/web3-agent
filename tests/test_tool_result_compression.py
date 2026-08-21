from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from chaincloud_agent_service.agent.context_builder import ContextBuilder
from chaincloud_agent_service.agent.tool_results import (
    FileToolResultStore,
    process_tool_result,
    tool_message_for_context,
    tool_result_metadata,
)


def _processed(tmp_path, tool_name: str, content: str, threshold: int = 200):
    store = FileToolResultStore(tmp_path)
    message, metadata = process_tool_result(
        store=store, tool_name=tool_name, tool_args={"query": "demo"},
        raw_content=content, threshold_bytes=threshold, preview_chars=80,
    )
    message.tool_call_id = "call-1"
    return store, message, metadata


def test_small_result_stays_uncompressed_but_is_traceable(tmp_path) -> None:
    raw = '{"rows":[{"id":1}]}'
    store, message, metadata = _processed(tmp_path, "postgres_select", raw)

    assert message.content == raw
    assert metadata["compressed"] is False
    assert store.read(metadata["result_id"]) == raw
    assert metadata["result_contract"]["result_kind"] == "tabular_query"
    assert metadata["result_contract"]["provenance_complete"] is True


def test_large_sql_result_is_compressed_with_structured_facts(tmp_path) -> None:
    raw = json.dumps({"rows": [{"id": index, "amount": index * 10} for index in range(100)]})
    _, message, metadata = _processed(tmp_path, "postgres_select", raw)
    payload = json.loads(message.content)

    assert metadata["compressed"] is True
    assert payload["key_facts"]["rows_count"] == 100
    assert payload["result_id"] == metadata["result_id"]
    assert len(message.content) < len(raw)


@pytest.mark.parametrize("tool_name", ["ethereum_jsonrpc", "web_search"])
def test_large_rpc_and_web_results_are_compressed(tmp_path, tool_name: str) -> None:
    raw = json.dumps({"results": [{"hash": f"0x{index:064x}", "text": "x" * 100} for index in range(30)]})
    _, message, metadata = _processed(tmp_path, tool_name, raw)
    payload = json.loads(message.content)

    assert payload["compressed"] is True
    assert payload["evidence_level"] in {"onchain_rpc", "public_source"}
    assert metadata["structured_facts"]["results_count"] == 30


def test_current_step_dependency_is_kept_as_protected_evidence(tmp_path) -> None:
    raw = '{"rows":[{"critical_value":42}]}'
    _, dependency, _ = _processed(tmp_path, "postgres_select", raw, threshold=10_000)
    builder = ContextBuilder("unknown-model", 4000, 3000, 500)
    result = builder.executor(
        scene="planned_executor", system_prompt="rules", current_request="analyse",
        critical_state="step depends on query", messages=[HumanMessage(content="analyse")],
        dependency_evidence=[dependency],
    )

    assert dependency in result.messages
    assert result.audit["category_tokens"]["dependency_evidence"] > 0


def test_old_tool_result_is_compacted_semantically(tmp_path) -> None:
    raw = json.dumps({"rows": [{"id": index} for index in range(10)]})
    _, old_result, metadata = _processed(tmp_path, "postgres_select", raw, threshold=10_000)
    builder = ContextBuilder("unknown-model", 4000, 3000, 500)
    result = builder.executor(
        scene="direct_executor", system_prompt="rules", current_request="new request",
        critical_state="safe", messages=[
            HumanMessage(content="old request"),
            AIMessage(content="", tool_calls=[{"id": "call-1", "name": "postgres_select", "args": {}}]),
            old_result,
            HumanMessage(content="new request"),
        ],
    )
    compacted = next(message for message in result.messages if isinstance(message, ToolMessage))

    assert "较早工具结果已压缩" in str(compacted.content)
    assert metadata["result_id"] in str(compacted.content)


def test_raw_result_remains_readable_after_active_message_compression(tmp_path) -> None:
    raw = "x" * 5000
    store, message, metadata = _processed(tmp_path, "web_search", raw)

    assert tool_result_metadata(message)["raw_result_location"] == metadata["raw_result_location"]
    assert store.read(metadata["result_id"]) == raw


def test_compression_significantly_reduces_context_tokens(tmp_path) -> None:
    raw = json.dumps({"rows": [{"payload": "long-value-" * 50} for _ in range(100)]})
    _, message, _ = _processed(tmp_path, "postgres_select", raw, threshold=200)
    builder = ContextBuilder("unknown-model", 100_000, 90_000, 5_000)
    raw_message = ToolMessage(content=raw, tool_call_id="call-1", name="postgres_select")

    assert builder.counter.message(message) < builder.counter.message(raw_message) * 0.2


def test_error_payload_is_not_compressed(tmp_path) -> None:
    error = {
        "status": "error", "error_type": "permission_error",
        "permission_error": True, "retryable": False, "message": "denied",
    }
    raw = json.dumps(error)
    store = FileToolResultStore(tmp_path)
    message, metadata = process_tool_result(
        store=store, tool_name="dangerous_tool", tool_args={}, raw_content=raw,
        threshold_bytes=1, preview_chars=20, error=error,
    )

    assert message.content == raw
    assert metadata["compressed"] is False
    assert json.loads(message.content)["permission_error"] is True


def test_context_summary_can_be_created_for_old_small_result(tmp_path) -> None:
    raw = '{"value":123}'
    _, message, metadata = _processed(tmp_path, "ethereum_jsonrpc", raw, threshold=1000)
    compacted = tool_message_for_context(message, compact_old=True)

    assert len(compacted.content) > 0
    assert metadata["result_id"] in str(compacted.content)


def _tron_raw_result() -> str:
    transfer_topic = "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    return json.dumps(
        {
            "provider": "tron_public_node",
            "txid": "a" * 64,
            "transaction": {
                "txID": "a" * 64,
                "signature": ["secret-signature-material"],
                "raw_data_hex": "deadbeef" * 100,
                "raw_data": {
                    "timestamp": 1_700_000_000_000,
                    "contract": [
                        {
                            "type": "TriggerSmartContract",
                            "parameter": {
                                "type_url": "type.googleapis.com/protocol.TriggerSmartContract",
                                "value": {
                                    "owner_address": "41" + "1" * 40,
                                    "contract_address": "41" + "2" * 40,
                                    "data": "private-call-data" * 20,
                                },
                            },
                        }
                    ],
                },
                "ret": [{"contractRet": "SUCCESS"}],
            },
            "transaction_info": {
                "id": "a" * 64,
                "blockNumber": 123,
                "blockTimeStamp": 1_700_000_000_100,
                "fee": 456,
                "contract_address": "41" + "3" * 40,
                "receipt": {
                    "result": "SUCCESS",
                    "energy_usage_total": 789,
                    "energy_fee": 100,
                    "net_fee": 20,
                    "opaque_receipt_payload": "do-not-expose",
                },
                "log": [
                    {
                        "address": "41" + "4" * 40,
                        "topics": [
                            transfer_topic,
                            "0" * 24 + "5" * 40,
                            "0" * 24 + "6" * 40,
                        ],
                        "data": f"{1_000_000:064x}",
                        "opaque_log_payload": "do-not-expose",
                    }
                ],
                "internal_transactions": [
                    {"hash": "internal-secret", "callValueInfo": [{"callValue": 1}]}
                ],
            },
        }
    )


def test_tron_transaction_result_always_uses_canonical_contract(tmp_path) -> None:
    raw = _tron_raw_result()
    store, message, metadata = _processed(
        tmp_path, "get_tron_transaction", raw, threshold=1_000_000
    )
    payload = json.loads(message.content)

    assert metadata["representation"] == "canonical_tron_transaction"
    assert metadata["compressed"] is True
    assert payload == metadata["structured_facts"]
    assert payload["result_id"] == metadata["result_id"]
    assert payload["txid"] == "a" * 64
    assert payload["transaction_status"] == "SUCCESS"
    assert payload["receipt_status"] == "SUCCESS"
    assert payload["block_number"] == 123
    assert payload["block_timestamp"] == 1_700_000_000_100
    assert payload["fee"] == 456
    assert payload["contract_type"] == "TriggerSmartContract"
    assert payload["owner_address"] == "41" + "1" * 40
    assert payload["contract_address"] == "41" + "2" * 40
    assert payload["internal_transaction_count"] == 1
    assert payload["log_count"] == 1
    assert payload["transfer_summaries"] == [
        {
            "log_index": 0,
            "token_contract": "41" + "4" * 40,
            "from_address": "41" + "5" * 40,
            "to_address": "41" + "6" * 40,
            "raw_amount": "1000000",
        }
    ]
    assert store.read(payload["result_id"]) == raw
    assert metadata["result_contract"]["terminal"] is True
    assert metadata["result_contract"]["structured_facts_complete"] is True
    assert metadata["result_contract"]["ambiguity"] == []


def test_tron_tool_message_excludes_full_raw_sections(tmp_path) -> None:
    raw = _tron_raw_result()
    _, message, metadata = _processed(
        tmp_path, "get_tron_transaction", raw, threshold=1_000_000
    )

    exposed = str(message.content)
    assert "signature" not in exposed
    assert "raw_data_hex" not in exposed
    assert "private-call-data" not in exposed
    assert "opaque_receipt_payload" not in exposed
    assert "opaque_log_payload" not in exposed
    assert "internal-secret" not in exposed
    assert metadata["context_summary"]["preview"] == ""
    assert "signature" not in json.dumps(metadata["structured_facts"])


def test_tron_canonical_result_stays_canonical_as_dependency_evidence(tmp_path) -> None:
    raw = _tron_raw_result()
    _, dependency, metadata = _processed(
        tmp_path, "get_tron_transaction", raw, threshold=1_000_000
    )
    builder = ContextBuilder("unknown-model", 4000, 3000, 500)
    result = builder.executor(
        scene="planned_executor",
        system_prompt="rules",
        current_request="analyse",
        critical_state="summarize receipt",
        messages=[HumanMessage(content="analyse")],
        dependency_evidence=[dependency],
    )

    dependency_message = next(
        message for message in result.messages if isinstance(message, ToolMessage)
    )
    assert dependency_message.content == dependency.content
    assert metadata["result_id"] in str(dependency_message.content)
    assert "raw_data_hex" not in str(dependency_message.content)
