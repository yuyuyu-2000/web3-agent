from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    def send(self, destination: str, payload: dict[str, Any]) -> None: ...


class FeishuNotifier:
    def __init__(self, timeout_sec: int = 10) -> None:
        self.timeout_sec = timeout_sec

    def send(self, destination: str, payload: dict[str, Any]) -> None:
        text = (
            f"ChainCloud 监控命中\n规则: {payload['rule_id']}\n"
            f"地址: {payload.get('address') or '-'}\n交易: {payload['transaction_hash']}\n"
            f"金额: {payload.get('amount') or '-'} {payload.get('token') or ''}\n"
            f"USD 金额: {payload.get('amount_usd') or '-'}\n链: {payload.get('chain') or '-'}\n"
            f"触发时间: {payload.get('triggered_at') or '-'}"
        )
        body = json.dumps({"msg_type": "text", "content": {"text": text}}, ensure_ascii=False).encode()
        request = Request(destination, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=self.timeout_sec) as response:  # noqa: S310 - user-owned webhook
            if response.status >= 400:
                raise RuntimeError(f"Feishu webhook returned HTTP {response.status}")


@dataclass(frozen=True)
class NotificationResult:
    success: bool
    error: str | None = None


class NotificationService:
    """Channel-neutral dispatcher. Destinations are resolved per user."""

    def __init__(self, notifiers: dict[str, Notifier]) -> None:
        self.notifiers = notifiers

    def send(self, channel: str, destination: str | None, payload: dict[str, Any]) -> NotificationResult:
        try:
            if not destination:
                raise ValueError(f"missing per-user {channel} notification destination")
            notifier = self.notifiers.get(channel)
            if notifier is None:
                raise ValueError(f"unsupported notification channel: {channel}")
            notifier.send(destination, payload)
            return NotificationResult(True)
        except Exception as exc:
            logger.warning("notification failed channel=%s error_type=%s", channel, exc.__class__.__name__)
            return NotificationResult(False, str(exc))

