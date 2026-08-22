"""Conservative, auditable validation for deterministic step results."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from chaincloud_agent_service.agent.planning.models import PlanStep, StepResult


MACHINE_STEP_VALIDATOR_VERSION = "1.0.0-shadow"
_HEX_64_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{64}$")
_FIELD_LIST_RE = re.compile(
    r"(?:required[ _-]?fields?|必填字段|必须包含字段|需要字段)\s*[:：=]\s*"
    r"([A-Za-z0-9_.,，、\s-]+)",
    re.IGNORECASE,
)
_FIELD_ALIASES = {
    "交易哈希": "txid",
    "tx hash": "txid",
    "transaction hash": "txid",
    "区块高度": "block_number",
    "交易状态": "transaction_status",
    "回执状态": "receipt_status",
}


class CheckedPredicate(BaseModel):
    name: str
    outcome: Literal["pass", "fail", "unknown"]
    detail: str = ""


class MachineValidationDecision(BaseModel):
    decision: Literal["pass", "fail", "unknown"]
    reason: str
    checked_predicates: list[CheckedPredicate] = Field(default_factory=list)
    validator_version: str = MACHINE_STEP_VALIDATOR_VERSION
    result_kind: str = "unknown"


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _values(facts: list[dict[str, Any]], field: str) -> list[Any]:
    aliases = {field.lower()}
    if field.lower() == "txid":
        aliases.add("tx_hash")
    return [
        value
        for key, value in _walk(facts)
        if key.lower() in aliases
        or (field.lower() == "txid" and key.lower().endswith("_tx_hash"))
    ]


def _nonempty(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _normalized_identifier(value: Any) -> str:
    return str(value).strip().lower().removeprefix("0x")


def _record_for_result(
    result: StepResult, tool_result_records: list[dict[str, Any]]
) -> dict[str, Any] | None:
    ids = {str(item.get("result_id")) for item in result.result_references}
    return next(
        (record for record in reversed(tool_result_records) if str(record.get("result_id")) in ids),
        None,
    )


def _required_fields(criteria: str) -> list[str]:
    fields: list[str] = []
    for match in _FIELD_LIST_RE.finditer(criteria):
        for item in re.split(r"[,，、\s]+", match.group(1).strip()):
            if item and item.lower() not in {"and", "or"}:
                normalized = item.lower()
                fields.append(_FIELD_ALIASES.get(normalized, normalized))
    return list(dict.fromkeys(fields))


def _criteria_clauses(criteria: str) -> list[str]:
    return [
        clause.strip()
        for clause in re.split(r"[；;。\n]+", criteria)
        if clause.strip()
    ]


def validate_deterministic_step(
    *,
    step: PlanStep,
    result: StepResult,
    prior_step_results: list[dict[str, Any]] | None = None,
    tool_result_records: list[dict[str, Any]] | None = None,
    last_tool_errors: list[dict[str, Any]] | None = None,
    tool_events: list[dict[str, Any]] | None = None,
) -> MachineValidationDecision:
    """Prove supported criteria; return unknown for all unsupported semantics."""
    checked: list[CheckedPredicate] = []
    records = tool_result_records or []
    record = _record_for_result(result, records)
    contract = record.get("result_contract", {}) if record else {}
    result_kind = str(contract.get("result_kind") or "unknown")

    def check(name: str, passed: bool, detail: str) -> None:
        checked.append(CheckedPredicate(name=name, outcome="pass" if passed else "fail", detail=detail))

    check("result_status", result.status == "success", f"status={result.status}")
    check("result_error", not result.error, "error is empty" if not result.error else "error is present")
    check("unresolved_tool_errors", not last_tool_errors, f"count={len(last_tool_errors or [])}")
    recovery_events = [
        event for event in (tool_events or [])
        if event.get("step_id") == step.id
        and (int(event.get("attempt", 1)) > 1 or event.get("recovered") or event.get("fallback_tool"))
    ]
    check("no_retry_or_recovery", not recovery_events, f"events={len(recovery_events)}")
    check("single_tool_result", len(result.tool_calls) == 1, f"count={len(result.tool_calls)}")
    tool_allowed = bool(result.tool_calls) and result.tool_calls[0] in step.suggested_tools
    check("planned_tool_name", tool_allowed, f"actual={result.tool_calls}; planned={step.suggested_tools}")

    reference_keys = ("result_id", "tool_name", "tool_args", "created_at", "evidence_source", "raw_result_location", "content_sha256")
    refs_complete = bool(result.result_references) and all(
        all(ref.get(key) not in (None, "") for key in reference_keys)
        for ref in result.result_references
    )
    check("provenance_and_result_id", refs_complete and bool(result.provenance), f"references={len(result.result_references)}; provenance={len(result.provenance)}")
    check("result_contract_present", bool(record and contract), f"result_kind={result_kind}")
    if contract:
        check("terminal", contract.get("terminal") is True, f"terminal={contract.get('terminal')}")
        check("not_truncated", contract.get("truncated") is False, f"truncated={contract.get('truncated')}")
        check("no_ambiguity", not contract.get("ambiguity"), f"ambiguity={contract.get('ambiguity') or []}")
        check("structured_facts_complete", contract.get("structured_facts_complete") is True, f"complete={contract.get('structured_facts_complete')}")

    criteria = step.success_criteria.strip()
    recognized_criteria = 0
    supported_clauses: set[str] = set()
    clauses = _criteria_clauses(criteria)
    required = _required_fields(criteria)
    for field in required:
        recognized_criteria += 1
        values = _values(result.structured_facts, field)
        check(f"required_field:{field}", bool(values) and all(_nonempty(value) for value in values), f"matches={len(values)}")
    for clause in clauses:
        if _FIELD_LIST_RE.search(clause):
            supported_clauses.add(clause)

    lowered = criteria.lower()
    if re.search(r"\brow_count\s*(?:==|=|为|是)?\s*1\b|单行|一行|单条|一条", lowered):
        recognized_criteria += 1
        row_counts = _values(result.structured_facts, "row_count")
        check("row_count_single", bool(row_counts) and all(value == 1 for value in row_counts), f"values={row_counts}")
        supported_clauses.update(
            clause for clause in clauses
            if re.search(r"\brow_count\s*(?:==|=|为|是)?\s*1\b|单行|一行|单条|一条", clause.lower())
        )
    if "scalar" in lowered or "标量" in lowered:
        recognized_criteria += 1
        scalar = result_kind == "scalar" or bool(_values(result.structured_facts, "value"))
        check("scalar_result", scalar, f"result_kind={result_kind}")
        supported_clauses.update(clause for clause in clauses if "scalar" in clause.lower() or "标量" in clause)

    identifier_requested = any(term in lowered for term in ("hash 格式", "hash格式", "哈希格式", "64 位", "64位", "64-character")) and any(term in lowered for term in ("txid", "tx_hash", "交易哈希", "transaction hash", "hash"))
    if identifier_requested:
        recognized_criteria += 1
        identifiers = _values(result.structured_facts, "txid")
        check("identifier_hash_format", bool(identifiers) and all(_HEX_64_RE.fullmatch(str(value)) for value in identifiers if _nonempty(value)), f"matches={len(identifiers)}")
        supported_clauses.update(
            clause for clause in clauses
            if any(term in clause.lower() for term in ("格式", "64 位", "64位", "64-character"))
            and any(term in clause.lower() for term in ("txid", "tx_hash", "交易哈希", "transaction hash", "hash"))
        )

    status_requested = any(term in lowered for term in ("receipt", "回执", "transaction status", "交易状态")) and any(term in lowered for term in ("success", "成功"))
    if status_requested:
        recognized_criteria += 1
        tx_status = _values(result.structured_facts, "transaction_status")
        receipt_status = _values(result.structured_facts, "receipt_status")
        check("transaction_status_success", bool(tx_status) and all(str(value).upper() == "SUCCESS" for value in tx_status), f"values={tx_status}")
        check("receipt_status_success", bool(receipt_status) and all(str(value).upper() == "SUCCESS" for value in receipt_status), f"values={receipt_status}")
        supported_clauses.update(
            clause for clause in clauses
            if any(term in clause.lower() for term in ("receipt", "回执", "transaction status", "交易状态"))
            and any(term in clause.lower() for term in ("success", "成功"))
        )

    dependency_checks = 0
    prior_by_id = {str(item.get("step_id")): item for item in (prior_step_results or [])}
    current_args = result.result_references[0].get("tool_args", {}) if result.result_references else {}
    for dependency_id in step.depends_on:
        dependency = prior_by_id.get(dependency_id, {})
        dependency_facts = dependency.get("structured_facts", []) if isinstance(dependency, dict) else []
        expected_ids = _values(dependency_facts, "txid")
        actual_id = current_args.get("txid") if isinstance(current_args, dict) else None
        if expected_ids or actual_id is not None:
            dependency_checks += 1
            matches = bool(expected_ids) and actual_id is not None and any(
                _normalized_identifier(actual_id) == _normalized_identifier(expected)
                for expected in expected_ids
            )
            check(f"dependency_binding:{dependency_id}.tx_hash=tool_args.txid", matches, f"expected_count={len(expected_ids)}; actual_present={actual_id is not None}")
    recognized_criteria += dependency_checks
    if dependency_checks:
        supported_clauses.update(
            clause for clause in clauses
            if any(term in clause.lower() for term in ("dependency", "依赖", "一致", "=="))
            and any(term in clause.lower() for term in ("txid", "tx_hash", "交易哈希", "hash"))
        )

    if record and result.result_references:
        ref_args = result.result_references[0].get("tool_args")
        check("tool_args_record_match", ref_args == record.get("tool_args"), "reference args match persisted record" if ref_args == record.get("tool_args") else "reference args differ from persisted record")

    failures = [item for item in checked if item.outcome == "fail"]
    if failures:
        return MachineValidationDecision(
            decision="fail",
            reason="machine predicate failed: " + ", ".join(item.name for item in failures),
            checked_predicates=checked,
            result_kind=result_kind,
        )
    unsupported_clauses = [clause for clause in clauses if clause not in supported_clauses]
    if recognized_criteria == 0 or unsupported_clauses:
        checked.append(CheckedPredicate(
            name="success_criteria_machine_provable",
            outcome="unknown",
            detail="unsupported clauses: " + (" | ".join(unsupported_clauses) or criteria),
        ))
        return MachineValidationDecision(
            decision="unknown",
            reason="one or more success-criteria clauses are not machine-provable by this validator version",
            checked_predicates=checked,
            result_kind=result_kind,
        )
    return MachineValidationDecision(
        decision="pass",
        reason=f"all {recognized_criteria} supported success-criteria predicates passed",
        checked_predicates=checked,
        result_kind=result_kind,
    )
