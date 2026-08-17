from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from langchain_core.tools import StructuredTool

from chaincloud_agent_service.monitoring.runtime import current_monitor_user, monitor_store


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def create_monitor_rule_from_draft(draft: dict[str, Any], *, user_id: str) -> dict[str, Any]:
    """Create a formal rule from an already confirmed and validated draft."""
    rule = monitor_store().create_rule(
        user_id=user_id,
        rule_type=str(draft["rule_type"]),
        address=draft.get("address"),
        min_amount=(Decimal(str(draft["min_amount"])) if draft.get("min_amount") is not None else None),
        min_amount_usd=(Decimal(str(draft["min_amount_usd"])) if draft.get("min_amount_usd") is not None else None),
        chain=draft.get("chain"), token=draft.get("token"),
        notification_channel=str(draft.get("notification_channel") or "feishu"),
    )
    return {"status": "success", "rule": asdict(rule)}


def make_monitor_tools() -> list[StructuredTool]:
    def create_monitor_rule(rule_type: str, address: str | None = None,
                            min_amount: float | None = None, min_amount_usd: float | None = None,
                            chain: str | None = None, token: str | None = None,
                            notification_channel: str = "feishu") -> str:
        return _json(create_monitor_rule_from_draft({
            "rule_type": rule_type, "address": address, "min_amount": min_amount,
            "min_amount_usd": min_amount_usd, "chain": chain, "token": token,
            "notification_channel": notification_channel,
        }, user_id=current_monitor_user()))

    def list_monitor_rules() -> str:
        return _json({"status": "success", "rules": [asdict(r) for r in monitor_store().list_rules(current_monitor_user())]})

    def delete_monitor_rule(rule_id: str) -> str:
        deleted = monitor_store().delete_rule(current_monitor_user(), rule_id)
        return _json({"status": "success" if deleted else "error", "deleted": deleted,
                      "message": None if deleted else "rule not found"})

    def set_monitor_rule_enabled(rule_id: str, enabled: bool) -> str:
        updated = monitor_store().set_enabled(current_monitor_user(), rule_id, enabled)
        return _json({"status": "success" if updated else "error", "updated": updated,
                      "message": None if updated else "rule not found"})

    return [
        StructuredTool.from_function(
            create_monitor_rule, name="create_monitor_rule",
            description="创建持久化交易监控规则。地址监控用 address_transaction；大额交易用 large_transaction，并提供 min_amount 或 min_amount_usd。需要副作用确认。",
        ),
        StructuredTool.from_function(list_monitor_rules, name="list_monitor_rules",
            description="查询当前登录用户自己的监控规则。"),
        StructuredTool.from_function(delete_monitor_rule, name="delete_monitor_rule",
            description="删除当前登录用户自己的监控规则。需要副作用确认。"),
        StructuredTool.from_function(set_monitor_rule_enabled, name="set_monitor_rule_enabled",
            description="启用或禁用当前登录用户自己的监控规则。需要副作用确认。"),
    ]
