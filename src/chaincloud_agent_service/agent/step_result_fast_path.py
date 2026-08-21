"""Deterministic Planned StepResult construction for complete tool contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

from langchain_core.messages import ToolMessage

from chaincloud_agent_service.agent.planning.models import PlanStep, StepResult
from chaincloud_agent_service.agent.tool_results import tool_result_metadata


@dataclass(frozen=True)
class FastPathDecision:
    hit: bool
    reason: str
    result: StepResult | None = None


def _reference(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata.get(key)
        for key in (
            "result_id", "tool_name", "tool_args", "created_at",
            "evidence_source", "raw_result_location", "content_sha256",
        )
    }


def build_deterministic_step_result(
    *, step: PlanStep, step_messages: Sequence[Any],
    last_tool_errors: Sequence[dict[str, Any]], step_tool_call_count: int,
) -> FastPathDecision:
    """Return a StepResult only when generic state and result capabilities prove safety."""
    if last_tool_errors:
        return FastPathDecision(False, "unresolved_tool_error")
    if step_tool_call_count != 1:
        return FastPathDecision(False, "requires_single_tool_call")
    if int(step.estimated_tool_calls) != 1:
        return FastPathDecision(False, "plan_may_require_additional_tool")

    tool_messages = [
        message for message in step_messages
        if isinstance(message, ToolMessage) or getattr(message, "type", None) == "tool"
    ]
    if len(tool_messages) != 1:
        return FastPathDecision(False, "requires_single_tool_result")

    message = tool_messages[0]
    metadata = tool_result_metadata(message)
    if metadata is None:
        return FastPathDecision(False, "missing_result_metadata")
    contract = metadata.get("result_contract")
    if not isinstance(contract, dict):
        return FastPathDecision(False, "missing_result_contract")
    checks = (
        ("non_terminal_result", contract.get("terminal") is True),
        ("incomplete_structured_facts", contract.get("structured_facts_complete") is True),
        ("truncated_result", contract.get("truncated") is False),
        ("ambiguous_result", not contract.get("ambiguity")),
        ("incomplete_provenance", contract.get("provenance_complete") is True),
        ("contract_not_eligible", contract.get("deterministic_step_result_eligible") is True),
    )
    for reason, passed in checks:
        if not passed:
            return FastPathDecision(False, reason)

    facts = metadata.get("structured_facts")
    if not isinstance(facts, dict):
        return FastPathDecision(False, "invalid_structured_facts")
    kind = str(contract.get("result_kind") or "unknown")
    if kind == "tabular_query":
        if contract.get("read_only") is not True:
            return FastPathDecision(False, "result_not_read_only")
        if contract.get("row_count") != 1:
            return FastPathDecision(False, "postgres_result_not_single_row")
    elif kind != "canonical_transaction":
        return FastPathDecision(False, "unsupported_contract_capability")

    reference = _reference(metadata)
    if any(reference.get(key) in (None, "") for key in (
        "result_id", "tool_name", "created_at", "evidence_source",
        "raw_result_location", "content_sha256",
    )):
        return FastPathDecision(False, "incomplete_result_reference")

    tool_name = str(metadata.get("tool_name") or getattr(message, "name", "unknown_tool"))
    summary = (
        f"工具 {tool_name} 已成功返回完整、无歧义的结构化结果；"
        f"原始证据可通过 result_id={metadata['result_id']} 追溯。"
    )
    evidence_payload = {
        "result_id": metadata["result_id"],
        "result_kind": kind,
        "structured_facts": facts,
    }
    result = StepResult(
        step_id=step.id,
        status="success",
        summary=summary,
        evidence=[json.dumps(evidence_payload, ensure_ascii=False, default=str)],
        structured_facts=[facts],
        dependency_outputs={step.id: facts},
        result_references=[reference],
        provenance=[reference],
        tool_calls=[tool_name],
    )
    return FastPathDecision(True, "eligible_complete_contract", result)
