"""Deterministic pre-execution state validation for planned steps."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

from chaincloud_agent_service.agent.planning.models import PlanStep


ValidationAction = Literal["VALID", "MISSING"]
BlockResolution = Literal["clarification", "partial", "fail"]

_TRANSACTION_ID_RE = re.compile(r"(?<![0-9a-fA-F])(?:0x)?[0-9a-fA-F]{64}(?![0-9a-fA-F])")
_ADDRESS_RE = re.compile(
    r"(?<![0-9A-Za-z])"
    r"(?:0x[a-fA-F0-9]{40}|41[a-fA-F0-9]{40}|T[1-9A-HJ-NP-Za-km-z]{33})"
    r"(?![0-9A-Za-z])"
)
_TRANSACTION_FACT_KEYS = frozenset({"tx_hash", "deposit_tx_hash", "txid"})
_SCHEDULE_RE = re.compile(
    r"(?:每天|每日|每周|每月|定时|cron|\d{1,2}[:：]\d{2}|"
    r"\d{4}-\d{1,2}-\d{1,2}[T\s]\d{1,2})",
    re.IGNORECASE,
)
_DEICTIC_TRANSACTION_RE = re.compile(r"(?:这个|该|此)(?:交易|交易哈希|txid)", re.IGNORECASE)
_DEICTIC_ADDRESS_RE = re.compile(r"(?:这个|该|此)(?:地址|合约|账户)")


def _dependency_transaction_ids(dependency_results: list[dict[str, Any]]) -> list[str]:
    """Return typed transaction identifiers carried by dependency structured facts."""
    found: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).lower() in _TRANSACTION_FACT_KEYS and isinstance(nested, str):
                    candidate = nested.strip()
                    if _TRANSACTION_ID_RE.fullmatch(candidate):
                        found.append(candidate)
                else:
                    visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    for result in dependency_results:
        visit(result.get("structured_facts", []))
    return found


@dataclass(frozen=True)
class MissingState:
    field: str
    reason: str
    question: str
    expected_format: str


@dataclass(frozen=True)
class StateValidationDecision:
    action: ValidationAction
    step_id: str
    resolution: BlockResolution | None
    missing_state: list[MissingState]
    reason: str

    def model_dump(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def validate_step_state(
    step: PlanStep,
    *,
    conversation_text: str,
    dependency_results: list[dict[str, Any]],
    clarified_state: dict[str, Any] | None,
    available_tool_names: set[str],
) -> StateValidationDecision:
    """Validate state required before Executor without consulting an LLM."""
    unavailable = [name for name in step.suggested_tools if name not in available_tool_names]
    if unavailable:
        return StateValidationDecision(
            "MISSING", step.id, "fail", [],
            f"计划引用了当前不可用的工具：{', '.join(unavailable)}",
        )

    clarified = clarified_state or {}
    context = "\n".join(
        [
            conversation_text,
            step.objective,
            step.success_criteria,
            json.dumps(dependency_results, ensure_ascii=False, default=str),
            json.dumps(clarified, ensure_ascii=False, default=str),
        ]
    )
    missing: list[MissingState] = []

    schedule_value = str(clarified.get("schedule_spec", ""))
    if "add_scheduled_task" in step.suggested_tools and not (
        _SCHEDULE_RE.search(schedule_value) or _SCHEDULE_RE.search(context)
    ):
        missing.append(
            MissingState(
                "schedule_spec",
                "创建定时任务前必须明确执行时间或周期",
                "请补充任务何时执行，例如“每天 08:00”或具体 ISO 时间。",
                "自然语言周期、HH:MM 或 ISO 8601 时间",
            )
        )

    step_text = f"{step.objective} {step.success_criteria}"
    needs_transaction = (
        "get_tron_transaction" in step.suggested_tools
        or bool(_DEICTIC_TRANSACTION_RE.search(step_text))
    )
    needs_address = bool(_DEICTIC_ADDRESS_RE.search(step_text))
    target_value = str(clarified.get("target_identifier", ""))
    free_text_value = str(clarified.get("free_text", ""))
    dependency_txids = _dependency_transaction_ids(dependency_results)
    if needs_transaction and not (
        dependency_txids
        or _TRANSACTION_ID_RE.search(target_value)
        or _TRANSACTION_ID_RE.search(free_text_value)
        or _TRANSACTION_ID_RE.search(context)
    ):
        missing.append(
            MissingState(
                "transaction_identifier",
                "步骤需要交易哈希，但依赖结果和上下文中没有可识别的交易哈希",
                "请提供 64 位十六进制交易哈希。",
                "可带 0x 前缀的 64 位十六进制交易哈希",
            )
        )
    if needs_address and not (
        _ADDRESS_RE.search(target_value)
        or _ADDRESS_RE.search(free_text_value)
        or _ADDRESS_RE.search(context)
    ):
        missing.append(
            MissingState(
                "address_identifier",
                "步骤引用了地址、合约或账户，但上下文中没有可识别的地址",
                "请提供目标地址或合约地址。",
                "0x 地址、TRON hex 地址或 Tron T 地址",
            )
        )

    if missing:
        return StateValidationDecision(
            "MISSING", step.id, "clarification", missing,
            "执行当前步骤所需的状态不完整",
        )
    return StateValidationDecision("VALID", step.id, None, [], "执行状态完整")
