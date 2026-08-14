from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from chaincloud_agent_service.agent.planning.models import Plan, PlanStep
from chaincloud_agent_service.agent.planning.validator import validate_plan
from chaincloud_agent_service.agent.rolling_summary import is_context_length_error


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

PLANNER_SYSTEM_PROMPT = """你是任务规划器。请把用户目标转换为可执行计划。
只输出一个 JSON 对象，不要输出 Markdown、解释或思维过程。
JSON 格式：
{
  "goal": "最终目标",
  "steps": [
    {
      "id": "step_1",
      "objective": "本步骤要完成什么",
      "success_criteria": "如何判断本步骤完成",
      "suggested_tools": ["工具名称"],
      "depends_on": [],
      "requires_confirmation": false,
      "critical": true,
      "fallback_tools": [],
      "estimated_tool_calls": 4,
      "budget_reason": ""
    }
  ]
}
规则：
1. 简单任务只生成一个步骤，不要过度拆分。
2. 最多生成 6 个步骤，每个步骤必须有明确成功标准。
3. 只能引用提供的工具名称；不确定时 suggested_tools 使用空数组。
4. Planner 不执行工具，也不直接回答用户问题。
5. 创建、修改外部状态的步骤必须设置 requires_confirmation=true。
6. critical 表示缺失该步骤结果是否会使目标无法可靠完成；有等价降级工具时写入 fallback_tools。
7. 每个步骤默认应能在最多 4 次工具调用内完成；如果明确需要更多，estimated_tool_calls 可大于 4，并必须在 budget_reason 说明必要性。运行时只有全局剩余预算充足才会批准扩大。不要机械地把每次工具调用拆成单独步骤。
8. 一个步骤只设一个主要、可验证的数据目标。定位数据源、确认结构、查询明细、统计分析如果合计会超过默认额度，应拆成有依赖关系的步骤。
9. 已知表名时不要重复调用列举表工具；能够用一条聚合 SQL 同时确认日期覆盖、记录数和金额分布时，不要拆成多次探查。
10. “大额”没有明确阈值时不要停止任务：优先采用 amount_usd >= 100000；若当日数据分布适合动态口径，可采用当日金额前 5%，并在最终结果中明确说明口径。
"""


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def _parse_plan(text: str) -> Plan:
    fenced = _JSON_FENCE_RE.search(text)
    candidate = fenced.group(1).strip() if fenced else text.strip()
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    return Plan.model_validate(json.loads(candidate))


def _tool_catalog(tools: list[Any]) -> str:
    if not tools:
        return "当前没有可用工具。"
    rows: list[str] = []
    for tool in tools:
        name = str(getattr(tool, "name", tool.__class__.__name__))
        description = str(getattr(tool, "description", "")).strip()
        rows.append(f"- {name}: {description[:300]}")
    return "\n".join(rows)


def create_plan(
    model: Any,
    user_goal: str,
    tools: list[Any],
    conversation_context: str = "",
    model_messages: list[Any] | None = None,
) -> tuple[Plan, int]:
    """Create and validate a plan, retrying once before safe single-step fallback."""

    tool_names = {str(getattr(tool, "name", tool.__class__.__name__)) for tool in tools}
    feedback = ""
    for attempt in range(1, 3):
        prompt = (
            f"用户当前目标：\n{user_goal}\n\n"
            f"近期对话背景（仅用于消解指代，不要改变当前目标）：\n"
            f"{conversation_context or '无'}\n\n"
            f"可用工具：\n{_tool_catalog(tools)}"
            f"{feedback}"
        )
        try:
            response = model.invoke(
                model_messages or [
                    SystemMessage(content=PLANNER_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ]
            )
            plan = _parse_plan(_message_text(response))
            return validate_plan(plan, tool_names), attempt
        except Exception as exc:
            if is_context_length_error(exc):
                raise
            feedback = f"\n\n上一次输出无效：{exc}。请修正后只输出合法 JSON。"

    fallback = Plan(
        goal=user_goal,
        steps=[
            PlanStep(
                id="step_1",
                objective=user_goal,
                success_criteria="形成能够直接回应用户目标的可靠结果",
            )
        ],
    )
    return fallback, 2
