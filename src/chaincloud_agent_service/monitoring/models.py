from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal


RuleType = Literal["address_transaction", "large_transaction"]


@dataclass(frozen=True)
class MonitorRule:
    rule_id: str
    user_id: str
    rule_type: RuleType
    address: str | None
    min_amount: Decimal | None
    min_amount_usd: Decimal | None
    chain: str | None
    token: str | None
    enabled: bool
    notification_channel: str
    created_at: datetime
    last_triggered_at: datetime | None = None


@dataclass(frozen=True)
class TransactionRecord:
    transaction_id: str
    transaction_hash: str
    from_address: str | None
    to_address: str | None
    amount: Decimal | None
    amount_usd: Decimal | None
    chain: str | None
    token: str | None
    occurred_at: datetime | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class NotificationEvent:
    event_id: str
    rule_id: str
    user_id: str
    transaction_id: str
    transaction_hash: str
    channel: str
    payload: dict[str, Any]
    status: str
    attempts: int

