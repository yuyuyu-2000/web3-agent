"""Deterministic permission policy for planned steps.

This module deliberately contains no model calls: permission decisions must be
auditable code rules, independent from Planner output quality.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from chaincloud_agent_service.agent.planning.models import PlanStep


PermissionAction = Literal["ALLOW", "NEED_CONFIRM", "DENY"]

_READ_ONLY_TOOLS = {
    "postgres_select", "postgres_list_tables", "postgres_table_schema",
    "search_knowledge", "web_search", "clickhouse_list_datasources",
    "clickhouse_select", "tron_node_request", "get_tron_transaction", "ethereum_jsonrpc",
    "contract_decode_tx_input",
    "list_monitor_rules",
}
_SIDE_EFFECT_TOOLS: dict[str, tuple[str, str]] = {
    "add_scheduled_task": ("high", "将创建并持久化一个定时任务，之后会自动执行"),
    "create_dashboard": ("medium", "将在服务端生成并保存新的仪表盘文件"),
    "generate_bar_chart": ("low", "将在服务端生成并保存图表文件"),
    "generate_time_series": ("low", "将在服务端生成并保存图表文件"),
    "generate_pie_chart": ("low", "将在服务端生成并保存图表文件"),
    "generate_multi_line_chart": ("low", "将在服务端生成并保存图表文件"),
    "generate_dual_axis_chart": ("low", "将在服务端生成并保存图表文件"),
    "generate_price_distribution_chart": ("low", "将在服务端生成并保存图表文件"),
    "generate_liquidation_simulation_chart": ("low", "将在服务端生成并保存图表文件"),
    "create_monitor_rule": ("medium", "将创建并持久化后台监控规则，并可能主动发送通知"),
    "delete_monitor_rule": ("medium", "将删除已有后台监控规则"),
    "set_monitor_rule_enabled": ("medium", "将修改后台监控规则启用状态"),
}
_DENY_HINTS = (
    "绕过权限", "越权", "窃取", "泄露密钥", "导出私钥", "删除所有",
    "清空数据库", "drop database", "bypass authorization", "steal credentials",
)
_SIDE_EFFECT_HINTS = (
    "创建", "新增", "修改", "更新", "删除", "发送", "发布", "转账", "授权",
    "定时任务", "schedule", "create", "update", "delete", "send", "transfer",
)


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    step_id: str
    tool_name: str
    risk_level: Literal["none", "low", "medium", "high", "critical"]
    reason: str
    operation_summary: str
    estimated_impact: str

    @property
    def approval_key(self) -> str:
        return f"{self.step_id}:{self.tool_name}"

    def model_dump(self) -> dict[str, str]:
        return asdict(self)


def evaluate_step_permission(
    step: PlanStep, approved_permission_keys: list[str] | None = None
) -> PermissionDecision:
    """Return the first blocking decision for a step, using code-only rules."""
    approved = set(approved_permission_keys or [])
    normalized = f"{step.objective} {step.success_criteria}".lower()
    if any(hint in normalized for hint in _DENY_HINTS):
        return PermissionDecision(
            "DENY", step.id, step.suggested_tools[0] if step.suggested_tools else "__step__",
            "critical", "操作目标包含明显越权或凭据窃取意图", step.objective,
            "请求将被阻止，不会执行任何工具",
        )

    candidates = list(step.suggested_tools)
    if not candidates and any(hint in normalized for hint in _SIDE_EFFECT_HINTS):
        candidates = ["__step__"]
    if step.requires_confirmation and not candidates:
        candidates = ["__step__"]
    for tool_name in candidates:
        if tool_name in _READ_ONLY_TOOLS:
            continue
        risk, impact = _SIDE_EFFECT_TOOLS.get(
            tool_name,
            ("high", "工具未被只读白名单覆盖，可能修改外部或本地状态"),
        )
        decision = PermissionDecision(
            "NEED_CONFIRM", step.id, tool_name, risk,
            "该操作具有副作用或未被识别为只读操作", step.objective, impact,
        )
        if decision.approval_key not in approved:
            return decision

    return PermissionDecision(
        "ALLOW", step.id, candidates[0] if candidates else "__none__", "none",
        "步骤仅使用只读操作，或其精确权限已获批准", step.objective,
        "不修改外部状态",
    )
