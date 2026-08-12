"""Agent planning, step execution, tool loop, and checkpoint compilation."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from chaincloud_agent_service.agent.answer_composer import acompose_final_answer
from chaincloud_agent_service.agent.evaluation import evaluate_step
from chaincloud_agent_service.agent.planning import Plan, StepResult, create_plan
from chaincloud_agent_service.agent.permission import evaluate_step_permission
from chaincloud_agent_service.agent.review import direct_requires_review, review_answer
from chaincloud_agent_service.agent.routing import decide_route
from chaincloud_agent_service.agent.schema_context import build_agent_system_prompt
from chaincloud_agent_service.agent.state import AgentState
from chaincloud_agent_service.agent.state_validation import validate_step_state
from chaincloud_agent_service.agent.tool_recovery import RecoveringToolNode, parse_tool_error
from chaincloud_agent_service.config import Settings
from chaincloud_agent_service.observability.trace import (
    append_trace_event,
    build_request_summary,
    traced_async_node,
    traced_node,
)
from chaincloud_agent_service.tools.registry import get_tools
from chaincloud_agent_service.monitoring.runtime import bind_monitor_user, reset_monitor_user


MAX_STEP_TOOL_CALLS = 4
MAX_DIRECT_TOOL_CALLS = 4
MAX_REPLANS = 1
MAX_REVIEW_ATTEMPTS = 2


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


def _is_cancellation(text: str) -> bool:
    normalized = text.strip().lower().rstrip("。.!！")
    return normalized in {
        "取消",
        "取消执行",
        "不用了",
        "停止",
        "cancel",
        "stop",
        "no",
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
        f"步骤重要性：{'关键' if step.critical else '非关键'}\n"
        f"允许的 fallback 工具：{', '.join(step.fallback_tools) or '无'}\n"
        f"依赖步骤结果：{json.dumps(dependencies, ensure_ascii=False)}\n"
        f"用户补充的执行状态：{json.dumps(state.get('clarified_state', {}), ensure_ascii=False)}\n"
        f"本步骤剩余工具调用额度：{MAX_STEP_TOOL_CALLS - state.get('step_tool_call_count', 0)}\n"
        "需要数据时调用工具；已有足够信息时直接给出本步骤的结果摘要。"
    )


def _execution_summary(state: AgentState) -> str:
    return json.dumps(
        {
            "execution_mode": state.get("execution_mode"),
            "route_reason": state.get("route_reason"),
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
    available_tool_names = {
        str(getattr(tool, "name", tool.__class__.__name__)) for tool in tools
    }
    system_prompt = build_agent_system_prompt(settings)
    base_model = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.openai_timeout_sec,
        max_retries=settings.openai_max_retries,
    )
    executor_model = base_model.bind_tools(tools) if tools else base_model
    max_tool_retries = int(getattr(settings, "max_tool_retries", 2))
    max_step_retries = int(getattr(settings, "max_step_retries", 2))
    max_total_tool_calls = int(getattr(settings, "max_total_tool_calls", 12))
    tool_node = RecoveringToolNode(tools, max_retries=max_tool_retries) if tools else None

    def prepare_request_node(state: AgentState) -> dict[str, Any]:
        goal = _latest_user_question(list(state["messages"]))
        if state.get("status") == "blocked_missing_state":
            if _is_cancellation(goal):
                return {
                    "route_action": "cancel",
                    "status": "failed",
                    "failure_reason": "用户已取消补充信息",
                }
            return {
                "route_action": "clarify",
                "clarified_state": {
                    **state.get("clarified_state", {}),
                    "free_text": goal,
                },
                "status": "executing",
                "failure_reason": None,
            }
        if (
            state.get("status") == "waiting_confirmation"
            and state.get("plan")
            and state.get("current_step_id")
        ):
            if _is_confirmation(goal):
                approved = list(state.get("approved_step_ids", []))
                current_step_id = str(state["current_step_id"])
                if current_step_id not in approved:
                    approved.append(current_step_id)
                return {
                    "route_action": "resume",
                    "execution_mode": "planned",
                    "route_reason": "恢复等待用户确认的计划",
                    "route_confidence": 1.0,
                    "route_source": "resume",
                    "route_signals": ["confirmation_resume"],
                    "approved_step_ids": approved,
                    "approved_permission_keys": [
                        *state.get("approved_permission_keys", []),
                        *(
                            [f"{state['pending_permission']['step_id']}:{state['pending_permission']['tool_name']}"]
                            if state.get("pending_permission") else []
                        ),
                    ],
                    "status": "planning",
                    "failure_reason": None,
                }
            if _is_cancellation(goal):
                return {
                    "route_action": "cancel",
                    "execution_mode": "planned",
                    "route_reason": "用户取消了等待确认的计划",
                    "route_confidence": 1.0,
                    "route_source": "resume",
                    "route_signals": ["confirmation_cancelled"],
                    "status": "failed",
                    "failure_reason": "用户已取消执行",
                }
        return {"route_action": "route"}

    def prepare_request_route(state: AgentState) -> str:
        return state.get("route_action", "route")

    def router_node(state: AgentState) -> dict[str, Any]:
        messages = list(state["messages"])
        decision = decide_route(
            base_model,
            _latest_user_question(messages),
            tools,
            requested_mode=state.get("requested_mode", "auto"),
            conversation_context=_planning_context(messages),
        )
        update: dict[str, Any] = {
            "execution_mode": decision.mode,
            "route_reason": decision.reason,
            "route_confidence": decision.confidence,
            "route_source": decision.source,
            "route_signals": decision.signals,
            "plan": None,
            "current_step_id": None,
            "approved_step_ids": [],
            "approved_permission_keys": [],
            "pending_permission": None,
            "permission_action": None,
            "clarified_state": {},
            "state_validation": None,
            "state_validation_action": None,
            "block_resolution": None,
            "step_results": [],
            "candidate_step_result": None,
            "planner_attempts": 0,
            "replanning_count": 0,
            "tool_call_count": 0,
            "last_tool_errors": [],
            "permission_failure": None,
            "direct_tool_call_count": 0,
            "step_tool_call_count": 0,
            "step_retry_count": 0,
            "evaluation_action": None,
            "evaluation_feedback": None,
            "review_required": False,
            "review_reason": None,
            "review_action": None,
            "review_feedback": None,
            "review_attempts": 0,
            "status": "planning" if decision.mode == "planned" else "executing",
            "failure_reason": None,
        }
        append_trace_event(state, update, "decision_events", {
            "trace_id": state.get("trace_id"), "thread_id": state.get("trace_thread_id"),
            "decision_type": "router", "action": decision.mode,
            "reason": decision.reason, "confidence": decision.confidence,
            "source": decision.source,
        })
        return update

    def router_route(state: AgentState) -> str:
        return state.get("execution_mode", "planned")

    def planner_node(state: AgentState) -> dict[str, Any]:
        goal = _latest_user_question(list(state["messages"]))
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
            "approved_permission_keys": [],
            "pending_permission": None,
            "permission_action": None,
            "clarified_state": {},
            "state_validation": None,
            "state_validation_action": None,
            "block_resolution": None,
            "step_results": [],
            "candidate_step_result": None,
            "step_message_start": len(state["messages"]),
            "planner_attempts": attempts,
            "tool_call_count": 0,
            "last_tool_errors": [],
            "permission_failure": None,
            "direct_tool_call_count": 0,
            "step_tool_call_count": 0,
            "step_retry_count": 0,
            "evaluation_action": None,
            "evaluation_feedback": None,
            "status": "planning",
            "failure_reason": None,
        }

    def direct_agent_node(state: AgentState) -> dict[str, list[Any]]:
        msgs: list[Any] = list(state["messages"])
        direct_prompt = (
            "当前请求已被路由为直接执行模式。请直接解决用户当前请求。"
            "需要数据时可以调用工具，但不要制定或展示任务计划。"
            f"本轮剩余工具调用额度："
            f"{MAX_DIRECT_TOOL_CALLS - state.get('direct_tool_call_count', 0)}。"
        )
        combined_prompt = "\n\n---\n\n".join(
            part for part in (system_prompt, direct_prompt) if part
        )
        if msgs and isinstance(msgs[0], SystemMessage):
            msgs[0] = SystemMessage(
                content=f"{combined_prompt}\n\n---\n\n长期记忆背景：\n{msgs[0].content}"
            )
        elif combined_prompt:
            msgs = [SystemMessage(content=combined_prompt), *msgs]
        return {"messages": [executor_model.invoke(msgs)]}

    def direct_agent_route(state: AgentState) -> str:
        messages = list(state["messages"])
        calls = _tool_calls(messages[-1]) if messages else []
        if not calls:
            return "complete_direct"
        if not tools:
            return "budget_exceeded"
        if state.get("direct_tool_call_count", 0) + len(calls) > MAX_DIRECT_TOOL_CALLS:
            return "budget_exceeded"
        return "tools"

    def complete_direct_node(state: AgentState) -> dict[str, Any]:
        messages = list(state["messages"])
        summary = _message_text(messages[-1]) if messages else ""
        unresolved_errors = state.get("last_tool_errors", [])
        return {
            "status": "degraded" if unresolved_errors else ("completed" if summary else "failed"),
            "failure_reason": (
                "工具失败后未获得可靠替代结果"
                if unresolved_errors
                else (None if summary else "直接执行没有生成有效结果")
            ),
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
            return {
                "current_step_id": step.id,
                "step_tool_call_count": 0,
                "step_retry_count": 0,
                "evaluation_action": None,
                "evaluation_feedback": None,
                "last_tool_errors": [],
                "step_message_start": len(state["messages"]),
                "clarified_state": {},
                "state_validation": None,
                "state_validation_action": None,
                "block_resolution": None,
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
        return "permission" if state.get("status") == "executing" else "compose"

    def permission_gate_node(state: AgentState) -> dict[str, Any]:
        plan = _plan_from_state(state)
        current_step_id = state.get("current_step_id")
        step = next(item for item in plan.steps if item.id == current_step_id)
        decision = evaluate_step_permission(
            step, state.get("approved_permission_keys", [])
        )
        update: dict[str, Any] = {
            "permission_action": decision.action,
            "pending_permission": None,
        }
        if decision.action == "NEED_CONFIRM":
            update.update(
                status="waiting_confirmation",
                failure_reason=decision.reason,
                pending_permission=decision.model_dump(),
            )
        elif decision.action == "DENY":
            update.update(
                status="permission_denied",
                failure_reason=decision.reason,
                pending_permission=decision.model_dump(),
            )
        else:
            update.update(status="executing", failure_reason=None)
        append_trace_event(state, update, "decision_events", {
            "trace_id": state.get("trace_id"), "thread_id": state.get("trace_thread_id"),
            "decision_type": "permission_gate", "action": decision.action.lower(),
            "risk_level": getattr(decision, "risk_level", None), "reason": decision.reason,
            "step_id": current_step_id,
        })
        if decision.action == "DENY":
            append_trace_event(state, update, "error_events", {
                "trace_id": state.get("trace_id"),
                "thread_id": state.get("trace_thread_id"),
                "source": "permission_gate", "step_id": current_step_id,
                "error_type": "permission_denied", "retryable": False,
            })
        return update

    def permission_gate_route(state: AgentState) -> str:
        return "validate" if state.get("permission_action") == "ALLOW" else "finish"

    def state_validation_node(state: AgentState) -> dict[str, Any]:
        plan = _plan_from_state(state)
        current_step_id = state.get("current_step_id")
        step = next(item for item in plan.steps if item.id == current_step_id)
        messages = list(state["messages"])
        conversation_text = "\n".join(_message_text(message) for message in messages)
        decision = validate_step_state(
            step,
            conversation_text=conversation_text,
            dependency_results=state.get("step_results", []),
            clarified_state=state.get("clarified_state", {}),
            available_tool_names=available_tool_names,
        )
        return {
            "state_validation": decision.model_dump(),
            "state_validation_action": decision.action,
            "block_resolution": decision.resolution,
            "status": "executing" if decision.action == "VALID" else "blocked_missing_state",
            "failure_reason": None if decision.action == "VALID" else decision.reason,
        }

    def state_validation_route(state: AgentState) -> str:
        return "execute" if state.get("state_validation_action") == "VALID" else "blocked"

    def blocked_missing_state_node(state: AgentState) -> dict[str, Any]:
        resolution = state.get("block_resolution") or "fail"
        if resolution == "clarification":
            return {"status": "blocked_missing_state"}
        if resolution == "partial":
            return {"status": "partial"}
        return {"status": "failed"}

    def blocked_missing_state_route(state: AgentState) -> str:
        return "wait" if state.get("status") == "blocked_missing_state" else "finish"

    def executor_node(state: AgentState) -> dict[str, list[Any]]:
        msgs: list[Any] = list(state["messages"])
        execution_prompt = _step_execution_prompt(state)
        if state.get("evaluation_feedback"):
            execution_prompt += (
                "\n上一次 Evaluator 反馈："
                f"{state['evaluation_feedback']}\n请针对反馈修正本次执行结果。"
            )
        if state.get("last_tool_errors"):
            execution_prompt += (
                "\n最近工具错误（结构化事实）："
                f"{json.dumps(state['last_tool_errors'], ensure_ascii=False)}\n"
                "仅瞬时错误由工具层自动重试。请做语义修复：可修正参数、补充查询或使用计划声明的 fallback。"
                "权限/guardrail 错误严禁用等价工具绕过；缺少关键结果时不得编造。"
            )
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
        if state.get("tool_call_count", 0) + len(calls) > max_total_tool_calls:
            return "budget_exceeded"
        if state.get("step_tool_call_count", 0) + len(calls) > MAX_STEP_TOOL_CALLS:
            return "budget_exceeded"
        return "tools"

    def tools_node(state: AgentState) -> dict[str, Any]:
        assert tool_node is not None
        global_remaining = max(0, max_total_tool_calls - state.get("tool_call_count", 0))
        if state.get("execution_mode") == "direct":
            mode_remaining = MAX_DIRECT_TOOL_CALLS - state.get("direct_tool_call_count", 0)
        else:
            mode_remaining = MAX_STEP_TOOL_CALLS - state.get("step_tool_call_count", 0)
        remaining = min(global_remaining, max(0, mode_remaining))
        fallback_tools: set[str] = set()
        if state.get("execution_mode") == "planned" and state.get("current_step_id"):
            plan = _plan_from_state(state)
            step = next(item for item in plan.steps if item.id == state.get("current_step_id"))
            fallback_tools = set(step.fallback_tools)
        user_token = bind_monitor_user(state.get("user_id"))
        try:
            result = tool_node.invoke(
                state, remaining_budget=remaining, step_id=state.get("current_step_id"),
                fallback_tools=fallback_tools,
            )
        finally:
            reset_monitor_user(user_token)
        attempts = int(result.get("attempts", 0))
        errors = [
            payload for message in result.get("messages", [])
            if (payload := parse_tool_error(message)) is not None
        ]
        update: dict[str, Any] = {
            "messages": result.get("messages", []),
            "tool_call_count": state.get("tool_call_count", 0) + attempts,
            "last_tool_errors": errors,
            "tool_events": [*state.get("tool_events", []), *result.get("tool_events", [])],
        }
        new_error_events = [
            {
                "trace_id": state.get("trace_id"),
                "thread_id": state.get("trace_thread_id"),
                "source": "tool", "tool_name": event.get("tool_name"),
                "step_id": event.get("step_id"), "error_type": event.get("error_type"),
                "retryable": event.get("retryable"),
            }
            for event in result.get("tool_events", []) if event.get("status") == "error"
        ]
        if new_error_events:
            update["error_events"] = [*state.get("error_events", []), *new_error_events]
        if state.get("execution_mode") == "direct":
            update["direct_tool_call_count"] = (
                state.get("direct_tool_call_count", 0) + attempts
            )
        else:
            update["step_tool_call_count"] = (
                state.get("step_tool_call_count", 0) + attempts
            )
        return update

    def tools_route(state: AgentState) -> str:
        permission_error = next(
            (item for item in state.get("last_tool_errors", []) if item.get("permission_error")),
            None,
        )
        if permission_error:
            return "permission_failure"
        return "direct" if state.get("execution_mode") == "direct" else "planned"

    def permission_failure_node(state: AgentState) -> dict[str, Any]:
        error = next(
            (item for item in state.get("last_tool_errors", []) if item.get("permission_error")),
            {"message": "工具权限被拒绝"},
        )
        return {
            "permission_failure": error,
            "status": "permission_denied",
            "failure_reason": str(error.get("message") or "工具权限被拒绝"),
        }

    def budget_exceeded_node(state: AgentState) -> dict[str, Any]:
        messages = list(state["messages"])
        calls = _tool_calls(messages[-1]) if messages else []
        rejected = [
            ToolMessage(
                content=json.dumps(
                    {
                        "status": "error",
                        "tool": str(_call_field(call, "name") or "unknown_tool"),
                        "error_type": "budget_exhausted",
                        "retryable": False,
                        "permission_error": False,
                        "message": "工具调用预算已达到上限，本次调用未执行。",
                        "attempts": 0,
                    },
                    ensure_ascii=False,
                ),
                tool_call_id=str(_call_field(call, "id") or f"rejected-{index}"),
                name=str(_call_field(call, "name") or "unknown_tool"),
            )
            for index, call in enumerate(calls)
        ]
        return {
            "messages": rejected,
            "status": "degraded",
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
        latest_by_tool: dict[str, dict[str, Any] | None] = {}
        for message in tool_messages:
            latest_by_tool[str(getattr(message, "name", None) or "unknown_tool")] = parse_tool_error(message)
        unresolved_errors = [item for item in latest_by_tool.values() if item is not None]
        plan = _plan_from_state(state)
        step = next(item for item in plan.steps if item.id == current_step_id)
        fallback_succeeded = any(
            name in step.fallback_tools and payload is None
            for name, payload in latest_by_tool.items()
        )
        if fallback_succeeded:
            unresolved_errors = []
        if unresolved_errors:
            status = "failed" if step.critical else "partial"
        result = StepResult(
            step_id=current_step_id,
            status=status,
            summary=summary or "执行器没有生成有效结果",
            evidence=[_message_text(message)[:2000] for message in tool_messages],
            tool_calls=[
                str(getattr(message, "name", None) or "unknown_tool")
                for message in tool_messages
            ],
            error=(json.dumps(unresolved_errors, ensure_ascii=False) if unresolved_errors else (None if summary else "empty executor response")),
        )
        return {"candidate_step_result": result.model_dump()}

    def evaluator_node(state: AgentState) -> dict[str, Any]:
        plan = _plan_from_state(state)
        current_step_id = state.get("current_step_id")
        step = next(item for item in plan.steps if item.id == current_step_id)
        candidate = StepResult.model_validate(state.get("candidate_step_result"))
        decision = evaluate_step(base_model, step, candidate)
        retry_count = state.get("step_retry_count", 0)
        replan_count = state.get("replanning_count", 0)
        action = decision.action
        if not step.critical and candidate.status == "partial":
            action = "partial"
            decision.feedback = "非关键步骤失败，保留已有可靠结果并以 degraded 状态结束"
        if action == "pass" and candidate.status != "success":
            if candidate.status == "failed" and retry_count < max_step_retries:
                action = "retry"
                decision.feedback = (
                    f"关键工具结果仍缺失。"
                    f"{'请尝试 fallback：' + ', '.join(step.fallback_tools) if step.fallback_tools else '请修正参数或确认无法继续，禁止编造结果。'}"
                )
            else:
                action = "fail" if step.critical else "partial"
        if action == "retry" and retry_count >= max_step_retries:
            action = "fail" if step.critical and candidate.status == "failed" else "partial"
            decision.feedback = "步骤已达到最大重试次数"
        if action == "replan" and replan_count >= MAX_REPLANS:
            action = "partial"
            decision.feedback = "任务已达到最大重新规划次数"

        update: dict[str, Any] = {
            "current_step_id": current_step_id,
            "evaluation_action": action,
            "evaluation_feedback": decision.feedback or decision.reason,
        }
        if action == "pass":
            accepted = candidate.model_copy(update={"status": "success"})
            update.update(
                step_results=[*state.get("step_results", []), accepted.model_dump()],
                candidate_step_result=None,
                status="executing",
                failure_reason=None,
            )
        elif action == "retry":
            update.update(
                step_retry_count=retry_count + 1,
                candidate_step_result=None,
                status="executing",
            )
        elif action == "replan":
            update.update(
                replanning_count=replan_count + 1,
                candidate_step_result=None,
                status="planning",
            )
        else:
            result_status = "partial" if action == "partial" else "failed"
            accepted = candidate.model_copy(update={"status": result_status})
            update.update(
                step_results=[*state.get("step_results", []), accepted.model_dump()],
                candidate_step_result=None,
                status=("degraded" if action == "partial" and not step.critical else ("partial" if action == "partial" else "failed")),
                failure_reason=decision.reason,
            )
        append_trace_event(state, update, "decision_events", {
            "trace_id": state.get("trace_id"), "thread_id": state.get("trace_thread_id"),
            "decision_type": "evaluator", "action": action,
            "reason": decision.feedback or decision.reason, "step_id": current_step_id,
        })
        if action in {"partial", "fail"}:
            append_trace_event(state, update, "error_events", {
                "trace_id": state.get("trace_id"),
                "thread_id": state.get("trace_thread_id"),
                "source": "evaluator", "step_id": current_step_id,
                "error_type": "step_partial" if action == "partial" else "step_failed",
                "retryable": False,
            })
        return update

    def evaluator_route(state: AgentState) -> str:
        action = state.get("evaluation_action")
        if action == "pass":
            return "next"
        if action == "retry":
            return "retry"
        if action == "replan":
            return "replan"
        return "finish"

    def replan_node(state: AgentState) -> dict[str, Any]:
        old_plan = _plan_from_state(state)
        context = json.dumps(
            {
                "completed_results": state.get("step_results", []),
                "evaluator_feedback": state.get("evaluation_feedback"),
                "instruction": "只规划尚未完成的剩余工作，不要重复已完成目标",
            },
            ensure_ascii=False,
        )
        plan, attempts = create_plan(
            base_model,
            old_plan.goal,
            tools,
            conversation_context=context,
        )
        return {
            "plan": plan.model_dump(),
            "current_step_id": None,
            "step_results": [],
            "candidate_step_result": None,
            "planner_attempts": state.get("planner_attempts", 0) + attempts,
            "step_retry_count": 0,
            "approved_permission_keys": [],
            "pending_permission": None,
            "permission_action": None,
            "clarified_state": {},
            "state_validation": None,
            "state_validation_action": None,
            "block_resolution": None,
            "evaluation_action": None,
            "status": "planning",
            "failure_reason": None,
        }

    def review_gate_node(state: AgentState) -> dict[str, Any]:
        status = state.get("status")
        if status in {"waiting_confirmation"} or state.get("failure_reason") == "用户已取消执行":
            required, reason = False, "确认或取消提示不需要最终答案审查"
        elif state.get("execution_mode") == "planned":
            required, reason = True, "Planned 结果需要证据和完整性审查"
        else:
            required, reason = direct_requires_review(
                _latest_user_question(list(state["messages"])),
                state.get("route_signals", []),
                state.get("direct_tool_call_count", 0),
            )
        return {
            "review_required": required,
            "review_reason": reason,
            "review_action": None,
            "review_feedback": None,
            "review_attempts": 0,
        }

    async def compose_answer_node(state: AgentState) -> dict[str, list[Any]]:
        messages = list(state["messages"])
        messages.append(
            AIMessage(content=f"结构化任务执行摘要：\n{_execution_summary(state)}")
        )
        if state.get("review_feedback"):
            messages.append(
                AIMessage(
                    content=(
                        "Reviewer 要求修订上一版回答：\n"
                        f"{state['review_feedback']}\n"
                        "请在不新增无依据事实的前提下重新生成最终回答。"
                    )
                )
            )
        response = await acompose_final_answer(base_model, messages)
        return {"messages": [response]}

    def after_compose_route(state: AgentState) -> str:
        return "review" if state.get("review_required") else "end"

    def reviewer_node(state: AgentState) -> dict[str, Any]:
        messages = list(state["messages"])
        answer = _message_text(messages[-1]) if messages else ""
        decision = review_answer(
            base_model,
            _latest_user_question(messages),
            answer,
            _execution_summary(state),
        )
        return {
            "review_action": decision.action,
            "review_feedback": decision.feedback or decision.reason,
            "review_attempts": state.get("review_attempts", 0) + 1,
        }

    def execution_completed_node(state: AgentState) -> dict[str, Any]:
        return {"request_summary": build_request_summary(state)}

    def reviewer_route(state: AgentState) -> str:
        if (
            state.get("review_action") == "revise"
            and state.get("review_attempts", 0) < MAX_REVIEW_ATTEMPTS
        ):
            return "revise"
        return "end"

    builder = StateGraph(AgentState)
    builder.add_node("prepare_request", prepare_request_node)
    builder.add_node("router", traced_node("router", router_node))
    builder.add_node("direct_agent", traced_node("direct_agent", direct_agent_node))
    builder.add_node("complete_direct", complete_direct_node)
    builder.add_node("planner", traced_node("planner", planner_node))
    builder.add_node("select_step", traced_node("select_step", select_step_node))
    builder.add_node("permission_gate", traced_node("permission_gate", permission_gate_node))
    builder.add_node("state_validation", state_validation_node)
    builder.add_node("blocked_missing_state", blocked_missing_state_node)
    builder.add_node("executor", traced_node("executor", executor_node))
    builder.add_node("complete_step", complete_step_node)
    builder.add_node("evaluator", traced_node("evaluator", evaluator_node))
    builder.add_node("replan", traced_node("replan", replan_node))
    builder.add_node("budget_exceeded", budget_exceeded_node)
    builder.add_node("permission_failure", permission_failure_node)
    builder.add_node("review_gate", review_gate_node)
    builder.add_node("compose_answer", traced_async_node("compose_answer", compose_answer_node))
    builder.add_node("reviewer", traced_node("reviewer", reviewer_node))
    builder.add_node("execution_completed", execution_completed_node)
    if tools:
        builder.add_node("tools", traced_node("tools", tools_node))

    builder.add_edge(START, "prepare_request")
    builder.add_conditional_edges(
        "prepare_request",
        prepare_request_route,
        {
            "route": "router",
            "resume": "select_step",
            "clarify": "state_validation",
            "cancel": "review_gate",
        },
    )
    builder.add_conditional_edges(
        "router",
        router_route,
        {"direct": "direct_agent", "planned": "planner"},
    )
    builder.add_conditional_edges(
        "direct_agent",
        direct_agent_route,
        {
            "complete_direct": "complete_direct",
            "budget_exceeded": "budget_exceeded",
            **({"tools": "tools"} if tools else {}),
        },
    )
    builder.add_edge("complete_direct", "review_gate")
    builder.add_edge("planner", "select_step")
    builder.add_conditional_edges(
        "select_step",
        select_step_route,
        {"permission": "permission_gate", "compose": "review_gate"},
    )
    builder.add_conditional_edges(
        "permission_gate",
        permission_gate_route,
        {"validate": "state_validation", "finish": "execution_completed"},
    )
    builder.add_conditional_edges(
        "state_validation",
        state_validation_route,
        {"execute": "executor", "blocked": "blocked_missing_state"},
    )
    builder.add_conditional_edges(
        "blocked_missing_state",
        blocked_missing_state_route,
        {"wait": "execution_completed", "finish": "review_gate"},
    )
    executor_destinations = {
        "complete_step": "complete_step",
        "budget_exceeded": "budget_exceeded",
    }
    if tools:
        executor_destinations["tools"] = "tools"
    builder.add_conditional_edges("executor", executor_route, executor_destinations)
    if tools:
        builder.add_conditional_edges(
            "tools",
            tools_route,
            {"direct": "direct_agent", "planned": "executor", "permission_failure": "permission_failure"},
        )
        builder.add_edge("permission_failure", "execution_completed")
    builder.add_edge("budget_exceeded", "review_gate")
    builder.add_edge("complete_step", "evaluator")
    builder.add_conditional_edges(
        "evaluator",
        evaluator_route,
        {
            "next": "select_step",
            "retry": "permission_gate",
            "replan": "replan",
            "finish": "review_gate",
        },
    )
    builder.add_edge("replan", "select_step")
    builder.add_edge("review_gate", "compose_answer")
    builder.add_conditional_edges(
        "compose_answer",
        after_compose_route,
        {"review": "reviewer", "end": "execution_completed"},
    )
    builder.add_conditional_edges(
        "reviewer",
        reviewer_route,
        {"revise": "compose_answer", "end": "execution_completed"},
    )
    builder.add_edge("execution_completed", END)
    return builder.compile(checkpointer=checkpointer)
