"""Three-layer tool results: raw persistence, structured facts, and LLM summaries."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from langchain_core.messages import ToolMessage

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RawToolResult:
    result_id: str
    tool_name: str
    tool_args: dict[str, Any]
    created_at: str
    evidence_source: str
    location: str
    content_sha256: str
    size_bytes: int


class ToolResultStore(Protocol):
    def save(self, *, tool_name: str, tool_args: dict[str, Any], raw_content: str,
             evidence_source: str) -> RawToolResult: ...
    def read(self, result_id: str) -> str: ...


class FileToolResultStore:
    """Audit-oriented filesystem store; each raw result is an immutable JSON file."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def save(self, *, tool_name: str, tool_args: dict[str, Any], raw_content: str,
             evidence_source: str) -> RawToolResult:
        self.directory.mkdir(parents=True, exist_ok=True)
        result_id = uuid.uuid4().hex
        created_at = utc_now_iso()
        path = self.directory / f"{result_id}.json"
        payload = {
            "result_id": result_id, "tool_name": tool_name, "tool_args": tool_args,
            "created_at": created_at, "evidence_source": evidence_source,
            "raw_content": raw_content,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        return RawToolResult(
            result_id=result_id, tool_name=tool_name, tool_args=tool_args,
            created_at=created_at, evidence_source=evidence_source,
            location=str(path.resolve()),
            content_sha256=hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
            size_bytes=len(raw_content.encode("utf-8")),
        )

    def read(self, result_id: str) -> str:
        if not result_id or any(char not in "0123456789abcdef" for char in result_id):
            raise ValueError("invalid result_id")
        path = self.directory / f"{result_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("result_id") != result_id:
            raise ValueError("tool result id mismatch")
        return str(payload["raw_content"])


class InMemoryToolResultStore:
    """Test/local alternative with the same traceability contract."""

    def __init__(self) -> None:
        self._results: dict[str, tuple[RawToolResult, str]] = {}

    def save(self, *, tool_name: str, tool_args: dict[str, Any], raw_content: str,
             evidence_source: str) -> RawToolResult:
        result_id = uuid.uuid4().hex
        record = RawToolResult(
            result_id=result_id, tool_name=tool_name, tool_args=tool_args,
            created_at=utc_now_iso(), evidence_source=evidence_source,
            location=f"memory://tool-results/{result_id}",
            content_sha256=hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
            size_bytes=len(raw_content.encode("utf-8")),
        )
        self._results[result_id] = (record, raw_content)
        return record

    def read(self, result_id: str) -> str:
        return self._results[result_id][1]


def extract_structured_facts(raw_content: str, *, max_facts: int = 20) -> dict[str, Any]:
    """Deterministically retain high-signal shape, identifiers, and scalar facts."""
    try:
        value = json.loads(raw_content)
    except json.JSONDecodeError:
        return {"text_preview": raw_content[:1000], "char_count": len(raw_content)}

    facts: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            if len(facts) >= max_facts:
                break
            if isinstance(item, (str, int, float, bool)) or item is None:
                facts[str(key)] = _bounded_value(item)
            elif isinstance(item, list):
                facts[f"{key}_count"] = len(item)
                if item and isinstance(item[0], dict):
                    facts[f"{key}_fields"] = list(item[0].keys())[:20]
                    facts[f"{key}_sample"] = _bounded_value(item[:2])
            elif isinstance(item, dict):
                facts[f"{key}_fields"] = list(item.keys())[:20]
                scalar = {k: v for k, v in item.items() if isinstance(v, (str, int, float, bool)) or v is None}
                if scalar:
                    facts[f"{key}_values"] = _bounded_value(dict(list(scalar.items())[:10]))
    elif isinstance(value, list):
        facts["row_count"] = len(value)
        if value:
            facts["sample"] = _bounded_value(value[:2])
            if isinstance(value[0], dict):
                facts["fields"] = list(value[0].keys())[:20]
    else:
        facts["value"] = _bounded_value(value)
    return facts


_TRC20_TRANSFER_TOPIC = (
    "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _unique_strings(values: list[Any], *, limit: int = 20) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _string_value(value)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _topic_address(topic: Any) -> str | None:
    text = _string_value(topic)
    if not text:
        return None
    normalized = text.removeprefix("0x")
    if len(normalized) < 40:
        return None
    address = normalized[-40:]
    if any(char not in "0123456789abcdefABCDEF" for char in address):
        return None
    return "41" + address.lower()


def _hex_integer(value: Any) -> str | None:
    text = _string_value(value)
    if not text:
        return None
    normalized = text.removeprefix("0x")
    try:
        return str(int(normalized, 16))
    except ValueError:
        return None


def _tron_transfer_summaries(logs: list[Any], *, limit: int = 10) -> list[dict[str, Any]]:
    transfers: list[dict[str, Any]] = []
    for index, item in enumerate(logs):
        if not isinstance(item, dict):
            continue
        topics = item.get("topics")
        if not isinstance(topics, list) or not topics:
            continue
        signature = str(topics[0]).lower().removeprefix("0x")
        if signature != _TRC20_TRANSFER_TOPIC:
            continue
        transfers.append(
            {
                "log_index": index,
                "token_contract": _string_value(item.get("address")),
                "from_address": _topic_address(topics[1]) if len(topics) > 1 else None,
                "to_address": _topic_address(topics[2]) if len(topics) > 2 else None,
                "raw_amount": _hex_integer(item.get("data")),
            }
        )
        if len(transfers) >= limit:
            break
    return transfers


def extract_tron_transaction_facts(
    raw_content: str, *, result_id: str
) -> dict[str, Any]:
    """Return a deterministic, bounded contract for TRON transaction lookup.

    The full RPC payload is intentionally not copied into these facts. It remains
    retrievable from the ToolResultStore by ``result_id``.
    """
    try:
        value = json.loads(raw_content)
    except json.JSONDecodeError:
        return {
            "result_id": result_id,
            "status": "invalid_json",
            "error": "TRON 节点结果不是合法 JSON",
        }
    if not isinstance(value, dict):
        return {
            "result_id": result_id,
            "status": "invalid_payload",
            "error": "TRON 节点结果不是 JSON 对象",
        }

    transaction = value.get("transaction")
    transaction = transaction if isinstance(transaction, dict) else {}
    transaction_info = value.get("transaction_info")
    transaction_info = transaction_info if isinstance(transaction_info, dict) else {}
    raw_data = transaction.get("raw_data")
    raw_data = raw_data if isinstance(raw_data, dict) else {}
    contracts = raw_data.get("contract")
    contracts = contracts if isinstance(contracts, list) else []
    first_contract = contracts[0] if contracts and isinstance(contracts[0], dict) else {}
    parameter = first_contract.get("parameter")
    parameter = parameter if isinstance(parameter, dict) else {}
    contract_value = parameter.get("value")
    contract_value = contract_value if isinstance(contract_value, dict) else {}
    receipt = transaction_info.get("receipt")
    receipt = receipt if isinstance(receipt, dict) else {}
    logs = transaction_info.get("log")
    logs = logs if isinstance(logs, list) else []
    internal = transaction_info.get("internal_transactions")
    internal = internal if isinstance(internal, list) else []
    transaction_ret = transaction.get("ret")
    transaction_ret = transaction_ret if isinstance(transaction_ret, list) else []
    first_ret = transaction_ret[0] if transaction_ret and isinstance(transaction_ret[0], dict) else {}

    contract_type = _string_value(first_contract.get("type"))
    if not contract_type:
        type_url = _string_value(parameter.get("type_url"))
        contract_type = type_url.rsplit(".", 1)[-1] if type_url else None

    transaction_status = _string_value(first_ret.get("contractRet"))
    receipt_status = _string_value(receipt.get("result"))
    if not receipt_status:
        receipt_status = _string_value(transaction_info.get("result"))

    owner_address = _string_value(contract_value.get("owner_address"))
    contract_address = _string_value(contract_value.get("contract_address"))
    result_contract_address = _string_value(transaction_info.get("contract_address"))
    transfers = _tron_transfer_summaries(logs)
    relevant_addresses = _unique_strings(
        [
            owner_address,
            contract_address,
            result_contract_address,
            *[item.get("token_contract") for item in transfers],
            *[item.get("from_address") for item in transfers],
            *[item.get("to_address") for item in transfers],
        ]
    )
    errors = value.get("errors")
    errors = errors if isinstance(errors, dict) else {}

    return {
        "result_id": result_id,
        "provider": _string_value(value.get("provider")),
        "txid": (
            _string_value(value.get("txid"))
            or _string_value(transaction.get("txID"))
            or _string_value(transaction_info.get("id"))
        ),
        "transaction_status": transaction_status,
        "receipt_status": receipt_status,
        "block_number": transaction_info.get("blockNumber"),
        "block_timestamp": transaction_info.get("blockTimeStamp"),
        "fee": transaction_info.get("fee"),
        "contract_type": contract_type,
        "owner_address": owner_address,
        "contract_address": contract_address,
        "result_contract_address": result_contract_address,
        "relevant_addresses": relevant_addresses,
        "transfer_summaries": transfers,
        "log_count": len(logs),
        "internal_transaction_count": len(internal),
        "energy_usage_total": receipt.get("energy_usage_total"),
        "energy_fee": receipt.get("energy_fee"),
        "net_fee": receipt.get("net_fee"),
        "errors": errors or None,
    }


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return "...[nested truncated]"
    if isinstance(value, str):
        return value if len(value) <= 500 else value[:500] + "...[truncated]"
    if isinstance(value, list):
        return [_bounded_value(item, depth=depth + 1) for item in value[:5]]
    if isinstance(value, dict):
        return {
            str(key): _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    return value


def build_context_summary(*, tool_name: str, status: str, facts: dict[str, Any],
                          raw: RawToolResult, preview: str, compressed: bool,
                          error: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "status": status,
        "tool_name": tool_name,
        "summary": "工具执行失败" if error else "工具执行成功；完整结果已保存，可按 result_id 追溯。",
        "key_facts": facts,
        "result_id": raw.result_id,
        "reference": raw.location,
        "created_at": raw.created_at,
        "evidence_level": raw.evidence_source,
        "preview": preview,
        "compressed": compressed,
    }
    if error:
        payload["error"] = error
    return payload


def _contains_bounded_truncation(value: Any) -> bool:
    if isinstance(value, str):
        return "[truncated]" in value or "[nested truncated]" in value
    if isinstance(value, list):
        return any(_contains_bounded_truncation(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_bounded_truncation(item) for item in value.values())
    return False


def _result_contract(
    *, tool_name: str, raw_content: str, facts: dict[str, Any],
    canonical_tron: bool, raw: RawToolResult, error: dict[str, Any] | None,
) -> dict[str, Any]:
    """Describe result capabilities for state-driven consumers.

    Tool-specific processing may establish the contract, while graph routing only
    consumes these generic capability flags. Unknown shapes remain non-finalizable.
    """
    provenance_complete = all(
        (raw.result_id, raw.location, raw.content_sha256, raw.evidence_source, raw.created_at)
    )
    ambiguity: list[str] = []
    kind = "unknown"
    terminal = error is None
    complete = False
    read_only = False
    row_count: int | None = None

    if canonical_tron:
        kind = "canonical_transaction"
        required = (
            "result_id", "provider", "txid", "block_number",
            "transaction_status", "receipt_status",
        )
        missing = [key for key in required if facts.get(key) in (None, "")]
        if missing:
            ambiguity.append("missing_terminal_fields:" + ",".join(missing))
        if facts.get("errors"):
            ambiguity.append("provider_errors")
        complete = not ambiguity and not _contains_bounded_truncation(facts)
    elif tool_name == "postgres_select" and error is None:
        kind = "tabular_query"
        read_only = True
        try:
            value = json.loads(raw_content)
        except json.JSONDecodeError:
            ambiguity.append("invalid_json")
        else:
            if isinstance(value, list):
                row_count = len(value)
                sample = facts.get("sample", [])
                fields = facts.get("fields", [])
                complete = (
                    row_count <= 1
                    and facts.get("row_count") == row_count
                    and sample == value
                    and (
                        row_count == 0
                        or (
                            isinstance(value[0], dict)
                            and fields == list(value[0].keys())[:20]
                            and len(value[0]) <= 20
                        )
                    )
                    and not _contains_bounded_truncation(facts)
                )
                if row_count > 1:
                    ambiguity.append("multi_row_result")
                elif not complete:
                    ambiguity.append("structured_facts_incomplete")
            else:
                ambiguity.append("non_tabular_result")

    return {
        "version": 1,
        "result_kind": kind,
        "terminal": terminal,
        "read_only": read_only,
        "structured_facts_complete": complete,
        "truncated": not complete,
        "ambiguity": ambiguity,
        "provenance_complete": provenance_complete,
        "row_count": row_count,
        "deterministic_step_result_eligible": (
            terminal and complete and provenance_complete and not ambiguity
        ),
    }


def process_tool_result(*, store: ToolResultStore, tool_name: str,
                        tool_args: dict[str, Any], raw_content: str,
                        threshold_bytes: int, preview_chars: int,
                        error: dict[str, Any] | None = None) -> tuple[ToolMessage, dict[str, Any]]:
    # Lazy import avoids a cycle: ContextBuilder -> tool_results -> answer_composer.
    from chaincloud_agent_service.agent.answer_composer.evidence import (
        classify_tool_evidence_level,
    )
    evidence = classify_tool_evidence_level(tool_name).value
    raw = store.save(tool_name=tool_name, tool_args=tool_args, raw_content=raw_content,
                     evidence_source=evidence)
    canonical_tron = tool_name == "get_tron_transaction" and error is None
    facts = (
        extract_tron_transaction_facts(raw_content, result_id=raw.result_id)
        if canonical_tron
        else extract_structured_facts(raw_content)
    )
    compressed = canonical_tron or (raw.size_bytes > threshold_bytes and error is None)
    summary = build_context_summary(
        tool_name=tool_name, status="error" if error else "success", facts=facts,
        raw=raw, preview=("" if canonical_tron else raw_content[:preview_chars]),
        compressed=compressed, error=error,
    )
    result_contract = _result_contract(
        tool_name=tool_name, raw_content=raw_content, facts=facts,
        canonical_tron=canonical_tron, raw=raw, error=error,
    )
    content = (
        json.dumps(facts, ensure_ascii=False, default=str)
        if canonical_tron
        else (json.dumps(summary, ensure_ascii=False, default=str) if compressed else raw_content)
    )
    metadata = {
        "result_id": raw.result_id, "tool_name": tool_name, "tool_args": tool_args,
        "created_at": raw.created_at, "evidence_source": evidence,
        "raw_result_location": raw.location, "raw_size_bytes": raw.size_bytes,
        "content_sha256": raw.content_sha256, "structured_facts": facts,
        "context_summary": summary, "compressed": compressed,
        "representation": "canonical_tron_transaction" if canonical_tron else "default",
        "result_contract": result_contract,
    }
    message = ToolMessage(content=content, tool_call_id="pending", name=tool_name,
                          additional_kwargs={"tool_result": metadata})
    return message, metadata


def tool_result_metadata(message: Any) -> dict[str, Any] | None:
    kwargs = getattr(message, "additional_kwargs", None)
    value = kwargs.get("tool_result") if isinstance(kwargs, dict) else None
    return value if isinstance(value, dict) else None


def tool_message_for_context(message: ToolMessage, *, require_raw: bool = False,
                             compact_old: bool = False) -> ToolMessage:
    metadata = tool_result_metadata(message)
    if not metadata or (require_raw and not compact_old):
        return message
    if not metadata.get("compressed") and not compact_old:
        return message
    summary = dict(metadata.get("context_summary") or {})
    if compact_old:
        summary["summary"] = "较早工具结果已压缩；完整内容可通过 result_id 追溯。"
        summary["preview"] = str(summary.get("preview", ""))[:300]
    return ToolMessage(
        content=json.dumps(summary, ensure_ascii=False, default=str),
        tool_call_id=str(getattr(message, "tool_call_id", "")),
        name=str(getattr(message, "name", "unknown_tool")),
        additional_kwargs=getattr(message, "additional_kwargs", {}),
    )
