from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EvidenceLevel(str, Enum):
    ONCHAIN_RPC = "onchain_rpc"
    COMPANY_DATABASE = "company_database"
    PUBLIC_SOURCE = "public_source"
    USER_PROVIDED = "user_provided"
    MODEL_INFERENCE = "model_inference"
    UNVERIFIED = "unverified"


class EvidenceConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class EvidenceItem:
    claim: str
    level: EvidenceLevel
    source: str
    confidence: EvidenceConfidence = EvidenceConfidence.MEDIUM
    scope: str | None = None
    caveat: str | None = None


def classify_tool_evidence_level(tool_name: str | None) -> EvidenceLevel:
    name = (tool_name or "").lower()

    if any(keyword in name for keyword in ["ethereum", "tron", "rpc", "chain"]):
        return EvidenceLevel.ONCHAIN_RPC

    if any(
        keyword in name
        for keyword in ["postgres", "clickhouse", "database", "db", "sql"]
    ):
        return EvidenceLevel.COMPANY_DATABASE

    if any(keyword in name for keyword in ["web", "search", "tavily"]):
        return EvidenceLevel.PUBLIC_SOURCE

    if any(keyword in name for keyword in ["contract", "decode", "parser"]):
        return EvidenceLevel.ONCHAIN_RPC

    return EvidenceLevel.UNVERIFIED


def evidence_level_label(level: EvidenceLevel) -> str:
    labels = {
        EvidenceLevel.ONCHAIN_RPC: "链上 RPC 确认",
        EvidenceLevel.COMPANY_DATABASE: "公司数据库确认",
        EvidenceLevel.PUBLIC_SOURCE: "公开资料支持",
        EvidenceLevel.USER_PROVIDED: "用户提供信息",
        EvidenceLevel.MODEL_INFERENCE: "模型推断",
        EvidenceLevel.UNVERIFIED: "待验证",
    }
    return labels[level]
