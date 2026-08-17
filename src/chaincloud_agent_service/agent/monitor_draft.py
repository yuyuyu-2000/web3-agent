"""Structured, checkpoint-friendly monitor rule drafts."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel


class MonitorDraft(BaseModel):
    rule_type: Literal["address_transaction", "large_transaction"]
    address: str | None = None
    min_amount: float | None = None
    min_amount_usd: float | None = None
    chain: str | None = None
    token: str | None = None
    notification_channel: str = "feishu"
    protocol: str | None = None


_CREATE_RE = re.compile(
    r"(?:监控|监听|告警|预警|monitor).*(?:交易|地址|金额|协议)|"
    r"(?:创建|添加|设置|帮我).*(?:监控|监听|告警|预警)", re.IGNORECASE,
)
_NON_CREATE_RE = re.compile(r"(?:查询|查看|列出|有哪些|删除|暂停|恢复|启用|禁用).*(?:监控|规则)")
_TOPIC_SWITCH_RE = re.compile(
    r"(?:先不管|先放着|稍后再说|换个话题|另外|顺便).*(?:查|查询|分析|告诉|看看|帮我)",
    re.IGNORECASE,
)


def is_monitor_creation_request(text: str) -> bool:
    return bool(_CREATE_RE.search(text)) and not bool(_NON_CREATE_RE.search(text))


def is_monitor_topic_switch(text: str) -> bool:
    return bool(_TOPIC_SWITCH_RE.search(text))


def _json_object(content: Any) -> dict[str, Any]:
    text = content if isinstance(content, str) else str(content)
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("monitor draft model did not return a JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("monitor draft output must be an object")
    return value


def create_monitor_draft(model: Any, request: str) -> dict[str, Any]:
    response = model.invoke([
        SystemMessage(content=(
            "你只负责把监控需求转换为 JSON。只输出对象，字段必须是："
            "rule_type(address_transaction|large_transaction), address, min_amount, "
            "min_amount_usd, chain, token, notification_channel, protocol。"
            "没有的信息填 null；通知渠道未说明时填 feishu。"
            "金额单位明确为 USD/美元时使用 min_amount_usd，否则使用 min_amount。"
            "10万、10w 都转换为 100000。不得调用工具。"
        )),
        HumanMessage(content=request),
    ])
    return MonitorDraft.model_validate(_json_object(getattr(response, "content", response))).model_dump()


def revise_monitor_draft(model: Any, current: dict[str, Any], revision: str) -> dict[str, Any]:
    response = model.invoke([
        SystemMessage(content=(
            "你只负责按用户修订更新监控草稿。只输出完整 JSON 对象。"
            "必须保留用户未提到的现有字段；明确说不限制/清除时才设为 null。"
            "字段仅限 rule_type,address,min_amount,min_amount_usd,chain,token,"
            "notification_channel,protocol。金额缩写需转换为数字。不得调用工具。"
        )),
        HumanMessage(content=(
            f"Current Draft:\n{json.dumps(current, ensure_ascii=False)}\n\n"
            f"User Revision:\n{revision}"
        )),
    ])
    parsed = _json_object(getattr(response, "content", response))
    merged = {**current, **parsed}
    return MonitorDraft.model_validate(merged).model_dump()


def validate_monitor_draft(draft: dict[str, Any]) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    rule_type = draft.get("rule_type")
    if rule_type not in {"address_transaction", "large_transaction"}:
        missing.append({"field": "rule_type", "reason": "规则类型无效"})
    if rule_type == "address_transaction" and not str(draft.get("address") or "").strip():
        missing.append({"field": "address", "reason": "地址交易监控必须提供地址"})
    if rule_type == "large_transaction" and draft.get("min_amount") is None and draft.get("min_amount_usd") is None:
        missing.append({"field": "amount_threshold", "reason": "大额交易监控必须提供金额阈值"})
    for field in ("min_amount", "min_amount_usd"):
        value = draft.get(field)
        if value is None:
            continue
        try:
            if Decimal(str(value)) <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            missing.append({"field": field, "reason": "金额阈值必须是大于 0 的数值"})
    if draft.get("notification_channel") != "feishu":
        missing.append({"field": "notification_channel", "reason": "当前仅支持 feishu 通知渠道"})
    return missing


def monitor_draft_hash(draft: dict[str, Any], version: int) -> str:
    raw = json.dumps(
        {"version": version, "draft": draft}, sort_keys=True,
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def monitor_draft_summary(draft: dict[str, Any]) -> str:
    target = "地址交易" if draft.get("rule_type") == "address_transaction" else "大额交易"
    filters = [str(draft.get("chain") or "不限链")]
    if draft.get("protocol"):
        filters.append(str(draft["protocol"]))
    if draft.get("token"):
        filters.append(str(draft["token"]))
    if draft.get("address"):
        filters.append(f"地址 {draft['address']}")
    if draft.get("min_amount_usd") is not None:
        filters.append(f"单笔 ≥ {draft['min_amount_usd']:,.0f} USD")
    elif draft.get("min_amount") is not None:
        filters.append(f"数量 ≥ {draft['min_amount']:,.0f}")
    return f"监控 {' / '.join(filters)} 的{target}，命中后通过 {draft.get('notification_channel', 'feishu')} 通知。"
