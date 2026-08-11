"""Agent planning, step execution, tool loop, and checkpoint compilation."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from chaincloud_agent_service.agent.answer_composer import acompose_final_answer
from chaincloud_agent_service.agent.planning import Plan, StepResult, create_plan
from chaincloud_agent_service.agent.schema_context import build_agent_system_prompt
from chaincloud_agent_service.agent.state import AgentState
from chaincloud_agent_service.config import Settings
from chaincloud_agent_service.tools.registry import get_tools


MAX_TOOL_CALLS = 12
MAX_STEP_TOOL_CALLS = 4


def _latest_user_question(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage) or getattr(message, "type", None) == "human":
            content = getattr(message, "content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def _is_confirmation(text: str) -> bool:
    normalized = text.strip().lower().rstrip("。.!！")
    return normalized in {
        "确认",
        "确认执行",
        "同意",
        "继续",
        "可以执行",
        "confirm",
        "proceed",
        "yes",
    }


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


def _planning_context(messages: list[Any]) -> str:
    rows: list[str] = []
    for message in messages[:-1][-8:]:
        if isinstance(message, ToolMessage) or getattr(message, "type", None) == "tool":
            continue
        role = getattr(message, "type", message.__class__.__name__)
        text = _message_text(message)
        if text:
            rows.append(f"{role}: {text[:1000]}")
    return "\n".join(rows)[-6000:]


def _tool_calls(message: Any) -> list[Any]:
    calls = getattr(message, "tool_calls", None)
    return list(calls) if calls else []


def _call_field(call: Any, name: str) -> Any:
    if isinstance(call, dict):
        return call.get(name)
    return getattr(call, name, None)


def _plan_from_state(state: AgentState) -> Plan:
    raw = state.get("plan")
    if raw is None:
        raise ValueError("missing execution plan")
    return Plan.model_validate(raw)


def _step_execution_prompt(state: AgentState) -> str:
    plan = _plan_from_state(state)
    current_id = state.get("current_step_id")
    step = next(item for item in plan.steps if item.id == current_id)
    dependencies = {
        item["step_id"]: item
        for item in state.get("step_results", [])
        if item.get("step_id") in step.depends_on
    }
    return (
        "你正在执行一个结构化计划中的单个步骤。只处理当前步骤，不要提前执行后续步骤。\n"
        f"总目标：{plan.goal}\n"
        f"当前步骤 ID：{step.id}\n"
        f"当前步骤目标：{step.objective}\n"
        f"成功标准：{step.success_criteria}\n"
        f"建议工具：{', '.join(step.suggested_tools) or '由你按需选择'}\n"
        f"依赖步骤结果：{json.dumps(dependencies, ensure_ascii=False)}\n"
        f"本步骤剩余工具调用额度：{MAX_STEP_TOOL_CALLS - state.get('step_tool_call_count', 0)}\n"
        "需要数据时调用工具；已有足够信息时直接给出本步骤的结果摘要。"
    )


def _execution_summary(state: AgentState) -> str:
    return json.dumps(
        {
            "plan": state.get("plan"),
            "step_results": state.get("step_results", []),
            "status": state.get("status"),
            "failure_reason": state.get("failure_reason"),
        },
        ensure_ascii=False,
        default=str,
    )


def compile_agent_graph(
    settings: Settings,
    checkpointer: BaseCheckpointSaver,
):
    tools = get_tools(settings)
    system_prompt = build_agent_system_prompt(settings)
    base_model = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.openai_timeout_sec,
        max_retries=settings.openai_max_retries,
    )
    executor_model = base_model.bind_tools(tools) if tools else base_model
    tool_node = ToolNode(tools) if tools else None

    def planner_node(state: AgentState) -> dict[str, Any]:
        goal = _latest_user_question(list(state["messages"]))
        if (
            state.get("status") == "waiting_confirmation"
            and state.get("plan")
            and state.get("current_step_id")
            and _is_confirmation(goal)
        ):
            approved = list(state.get("approved_step_ids", []))
            current_step_id = str(state["current_step_id"])
            if current_step_id not in approved:
                approved.append(current_step_id)
            return {
                "approved_step_ids": approved,
                "status": "planning",
                "failure_reason": None,
            }
        plan, attempts = create_plan(
            base_model,
            goal,
            tools,
            conversation_context=_planning_context(list(state["messages"])),
        )
        return {
            "plan": plan.model_dump(),
            "current_step_id": None,
            "approved_step_ids": [],
            "step_results": [],
            "step_message_start": len(state["messages"]),
            "planner_attempts": attempts,
            "tool_call_count": 0,
            "step_tool_call_count": 0,
            "status": "planning",
            "failure_reason": None,
        }

    def select_step_node(state: AgentState) -> dict[str, Any]:
        plan = _plan_from_state(state)
        results = {item["step_id"]: item for item in state.get("step_results", [])}
        for step in plan.steps:
            if step.id in results:
                continue
            dependencies_satisfied = all(
                results.get(dep, {}).get("status") == "success"
                for dep in step.depends_on
            )
            if not dependencies_satisfied:
                continue
            if (
                step.requires_confirmation
                and step.id not in state.get("approved_step_ids", [])
            ):
                return {
                    "current_step_id": step.id,
                    "status": "waiting_confirmation",
                    "failure_reason": f"步骤 {step.id} 需要用户确认后才能执行",
                }
            return {
                "current_step_id": step.id,
                "step_tool_call_count": 0,
                "step_message_start": len(state["messages"]),
                "status": "executing",
            }

        if len(results) == len(plan.steps):
            final_status = (
                "completed"
                if all(item.get("status") == "success" for item in results.values())
                else "partial"
            )
            return {"current_step_id": None, "status": final_status}
        return {
            "current_step_id": None,
            "status": "partial",
            "failure_reason": "剩余步骤的依赖条件未满足",
        }

    def select_step_route(state: AgentState) -> str:
        return "execute" if state.get("status") == "executing" else "compose"

    def executor_node(state: AgentState) -> dict[str, list[Any]]:
        msgs: list[Any] = list(state["messages"])
        execution_prompt = _step_execution_prompt(state)
        combined_prompt = "\n\n---\n\n".join(
            part for part in (system_prompt, execution_prompt) if part
        )
        if msgs and isinstance(msgs[0], SystemMessage):
            msgs[0] = SystemMessage(
                content=f"{combined_prompt}\n\n---\n\n长期记忆背景：\n{msgs[0].content}"
            )
        elif combined_prompt:
            msgs = [SystemMessage(content=combined_prompt), *msgs]
        return {"messages": [executor_model.invoke(msgs)]}

    def executor_route(state: AgentState) -> str:
        messages = list(state["messages"])
        calls = _tool_calls(messages[-1]) if messages else []
        if not calls:
            return "complete_step"
        if not tools:
            return "budget_exceeded"
        if state.get("tool_call_count", 0) + len(calls) > MAX_TOOL_CALLS:
            return "budget_exceeded"
        if state.get("step_tool_call_count", 0) + len(calls) > MAX_STEP_TOOL_CALLS:
            return "budget_exceeded"
        return "tools"

    def tools_node(state: AgentState) -> dict[str, Any]:
        assert tool_node is not None
        messages = list(state["messages"])
        count = len(_tool_calls(messages[-1])) if messages else 0
        result = tool_node.invoke(state)
        return {
            "messages": result.get("messages", []),
            "tool_call_count": state.get("tool_call_count", 0) + count,
            "step_tool_call_count": state.get("step_tool_call_count", 0) + count,
        }

    def budget_exceeded_node(state: AgentState) -> dict[str, Any]:
        messages = list(state["messages"])
        calls = _tool_calls(messages[-1]) if messages else []
        rejected = [
            ToolMessage(
                content="工具调用预算已达到上限，本次调用未执行。",
                tool_call_id=str(_call_field(call, "id") or f"rejected-{index}"),
                name=str(_call_field(call, "name") or "unknown_tool"),
            )
            for index, call in enumerate(calls)
        ]
        return {
            "messages": rejected,
            "status": "partial",
            "failure_reason": "工具调用预算已达到上限",
        }

    def complete_step_node(state: AgentState) -> dict[str, Any]:
        messages = list(state["messages"])
        summary = _message_text(messages[-1]) if messages else ""
        current_step_id = state.get("current_step_id") or "unknown"
        status = "success" if summary else "failed"
        step_messages = messages[state.get("step_message_start", 0) :]
        tool_messages = [
            message
            for message in step_messages
            if isinstance(message, ToolMessage)
            or getattr(message, "type", None) == "tool"
        ]
        result = StepResult(
            step_id=current_step_id,
            status=status,
            summary=summary or "执行器没有生成有效结果",
            evidence=[_message_text(message)[:2000] for message in tool_messages],
            tool_calls=[
                str(getattr(message, "name", None) or "unknown_tool")
                for message in tool_messages
            ],
            error=None if summary else "empty executor response",
        )
        return {
            "step_results": [*state.get("step_results", []), result.model_dump()],
            "status": "executing" if summary else "partial",
            "failure_reason": None if summary else "当前步骤没有生成有效结果",
        }

    def after_step_route(state: AgentState) -> str:
        return "next" if state.get("status") == "executing" else "compose"

    async def compose_answer_node(state: AgentState) -> dict[str, list[Any]]:
        messages = list(state["messages"])
        messages.append(
            AIMessage(content=f"结构化任务执行摘要：\n{_execution_summary(state)}")
        )
        response = await acompose_final_answer(base_model, messages)
        return {"messages": [response]}

    builder = StateGraph(AgentState)
    builder.add_node("planner", planner_node)
    builder.add_node("select_step", select_step_node)
    builder.add_node("executor", executor_node)
    builder.add_node("complete_step", complete_step_node)
    builder.add_node("budget_exceeded", budget_exceeded_node)
    builder.add_node("compose_answer", compose_answer_node)
    if tools:
        builder.add_node("tools", tools_node)

    builder.add_edge(START, "planner")
    builder.add_edge("planner", "select_step")
    builder.add_conditional_edges(
        "select_step",
        select_step_route,
        {"execute": "executor", "compose": "compose_answer"},
    )
    executor_destinations = {
        "complete_step": "complete_step",
        "budget_exceeded": "budget_exceeded",
    }
    if tools:
        executor_destinations["tools"] = "tools"
    builder.add_conditional_edges("executor", executor_route, executor_destinations)
    if tools:
        builder.add_edge("tools", "executor")
    builder.add_edge("budget_exceeded", "compose_answer")
    builder.add_conditional_edges(
        "complete_step",
        after_step_route,
        {"next": "select_step", "compose": "compose_answer"},
    )
    builder.add_edge("compose_answer", END)
    return builder.compile(checkpointer=checkpointer)
