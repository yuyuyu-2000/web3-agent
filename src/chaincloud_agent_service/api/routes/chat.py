"""聊天 HTTP：鉴权、thread_id、memory 注入、调用 graph.ainvoke。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from langchain_core.messages import AIMessageChunk, BaseMessage, HumanMessage
from openai import APIConnectionError, APIStatusError, OpenAIError
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from chaincloud_agent_service.api.auth import optional_authenticated_user_or_static_auth
from chaincloud_agent_service.auth.store import UserRecord
from chaincloud_agent_service.observability.trace import (
    ChatTraceEvent,
    execution_trace_from_state,
    extract_agent_trace,
    new_execution_context,
)

router = APIRouter()

_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_THINK_TAG_RE = re.compile(r"</?think>", re.IGNORECASE)
_CHART_URL_RE = re.compile(r'(?:https?://[^\s"\'<>)]*)?/charts/[^\s"\'<>)]*\.html')
_CHART_FILEPATH_RE = re.compile(r'[^\s"\'<>)]*charts[/\\][^\s"\'<>)]*\.html')


class ChatRequest(BaseModel):
    thread_id: str = Field(
        ..., min_length=1, description="会话线程 ID，对应 checkpoint 的 thread_id"
    )
    message: str = Field(..., min_length=1, description="用户本轮输入")
    planning: Literal["auto", "direct", "planned"] = Field(
        default="auto",
        description="执行模式：自动判断、直接执行或先规划后执行",
    )
    memory_key: str | None = Field(
        default=None,
        min_length=1,
        description="可选：需要注入的长期记忆 key",
    )
    debug: bool = Field(
        default=False, description="是否返回 Agent 执行 trace; 默认关闭"
    )
    trace_max_chars: int = Field(
        default=500,
        ge=50,
        le=2000,
        description="debug=true 时, 每条 trace 预览内容的最大字符数",
    )


class ChatResponse(BaseModel):
    reply: str
    status: str | None = None
    failure_reason: str | None = None
    failed_tools: list[dict[str, Any]] | None = None
    trace: list[ChatTraceEvent] | None = None
    execution_trace: dict[str, Any] | None = None
    permission_required: dict[str, Any] | None = None
    clarification_required: dict[str, Any] | None = None


class PermissionApprovalRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    step_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    decision: Literal["approve", "cancel"]


class ClarificationRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    step_id: str = Field(..., min_length=1)
    values: dict[str, Any] = Field(default_factory=dict)
    decision: Literal["submit", "cancel"] = "submit"


def _ndjson_event(event_type: str, **payload: Any) -> bytes:
    return (json.dumps({"type": event_type, **payload}, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def _result_metadata(result: dict[str, Any]) -> dict[str, Any]:
    failed_tools = list(result.get("last_tool_errors") or [])
    permission_failure = result.get("permission_failure")
    if isinstance(permission_failure, dict) and permission_failure not in failed_tools:
        failed_tools.append(permission_failure)
    return {
        "status": result.get("status"),
        "failure_reason": result.get("failure_reason"),
        "failed_tools": failed_tools or None,
    }


class _IncrementalReasoningStripper:
    """Remove <think> blocks without leaking split tags across stream chunks."""

    _KEEP_CHARS = len("</think>") - 1

    def __init__(self) -> None:
        self._buffer = ""
        self._inside_think = False

    def feed(self, text: str, *, final: bool = False) -> str:
        self._buffer += text
        output: list[str] = []

        while self._buffer:
            tag = "</think>" if self._inside_think else "<think>"
            index = self._buffer.lower().find(tag)
            if index >= 0:
                if not self._inside_think:
                    output.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(tag) :]
                self._inside_think = not self._inside_think
                continue

            if self._inside_think:
                if final:
                    self._buffer = ""
                elif len(self._buffer) > self._KEEP_CHARS:
                    self._buffer = self._buffer[-self._KEEP_CHARS :]
                break

            if final:
                output.append(self._buffer)
                self._buffer = ""
            elif len(self._buffer) > self._KEEP_CHARS:
                output.append(self._buffer[: -self._KEEP_CHARS])
                self._buffer = self._buffer[-self._KEEP_CHARS :]
            break

        return "".join(output)


def _message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def _strip_reasoning_blocks(text: str) -> str:
    return _THINK_TAG_RE.sub("", _THINK_BLOCK_RE.sub("", text)).strip()


def _chart_url_from_path(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    if "/charts/" not in normalized:
        return None
    filename = os.path.basename(normalized)
    if not filename.endswith(".html"):
        return None
    return f"/charts/{filename}"


def _collect_chart_urls_from_value(value: Any) -> list[str]:
    urls: list[str] = []

    if isinstance(value, dict):
        raw_url = value.get("url")
        if isinstance(raw_url, str) and raw_url.endswith(".html"):
            urls.append(raw_url)

        raw_filepath = value.get("filepath")
        if isinstance(raw_filepath, str):
            url = _chart_url_from_path(raw_filepath)
            if url:
                urls.append(url)

        for item in value.values():
            urls.extend(_collect_chart_urls_from_value(item))
        return urls

    if isinstance(value, list):
        for item in value:
            urls.extend(_collect_chart_urls_from_value(item))
        return urls

    if isinstance(value, str):
        urls.extend(match.group(0) for match in _CHART_URL_RE.finditer(value))
        for match in _CHART_FILEPATH_RE.finditer(value):
            url = _chart_url_from_path(match.group(0))
            if url:
                urls.append(url)
        return urls

    return urls


def _extract_chart_urls(messages: list[Any]) -> list[str]:
    urls: list[str] = []
    for msg in messages:
        content = getattr(msg, "content", "")
        text = _message_content_to_text(content)
        if not text:
            continue

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text

        urls.extend(_collect_chart_urls_from_value(parsed))

    deduped: list[str] = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped


def _append_chart_urls(text: str, chart_urls: list[str]) -> str:
    if not chart_urls:
        return text

    existing = set(_CHART_URL_RE.findall(text))
    missing = [url for url in chart_urls if url not in existing]
    if not missing:
        return text

    return "\n".join([text.rstrip(), "", *missing]).strip()


def _provider_error_detail(exc: APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        msg = body.get("message")
        if msg is not None:
            return str(msg)
    return exc.message


def _metadata_matches_user(metadata: dict[str, Any], user: UserRecord) -> bool:
    metadata_user_id = metadata.get("user_id")
    if isinstance(metadata_user_id, str) and metadata_user_id == user.user_id:
        return True
    metadata_username = metadata.get("username")
    if isinstance(metadata_username, str) and metadata_username == user.username:
        return True
    return False


def _memory_key_matches_legacy_user_prefix(memory_key: str, user: UserRecord) -> bool:
    safe_username = user.username.strip().lower()
    return bool(safe_username) and memory_key.startswith(f"{safe_username}-")


def _memory_belongs_to_user(record: Any, user: UserRecord) -> bool:
    return _metadata_matches_user(
        getattr(record, "metadata", {}) or {}, user
    ) or _memory_key_matches_legacy_user_prefix(getattr(record, "memory_key", ""), user)


def _build_input_messages(
    request: Request, body: ChatRequest, current_user: UserRecord | None = None
) -> list[BaseMessage]:
    messages: list[BaseMessage] = []

    if body.memory_key:
        memory_service = getattr(request.app.state, "memory_service", None)
        if memory_service is None:
            raise HTTPException(
                status_code=500, detail="memory service is not initialized"
            )
        record = memory_service.get_memory(body.memory_key)
        if record is None:
            raise HTTPException(status_code=404, detail="memory not found")
        if current_user is not None and not _memory_belongs_to_user(
            record, current_user
        ):
            raise HTTPException(status_code=404, detail="memory not found")
        messages.append(memory_service.build_memory_system_message_for_record(record))

    messages.append(HumanMessage(content=body.message))
    return messages


@router.post("/chat", response_model=ChatResponse, response_model_exclude_none=True)
async def chat(
    request: Request,
    body: ChatRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ChatResponse:
    current_user = optional_authenticated_user_or_static_auth(request, authorization)

    graph = request.app.state.graph
    config = {"configurable": {"thread_id": body.thread_id}}
    input_messages = _build_input_messages(request, body, current_user)
    execution_context = new_execution_context(body.thread_id)
    user_context = {"user_id": current_user.user_id} if current_user else {}

    try:
        result = await graph.ainvoke(
            {"messages": input_messages, "requested_mode": body.planning, **execution_context, **user_context},
            config=config,
        )
    except APIStatusError as exc:
        status = exc.status_code
        if status < 400 or status > 599:
            status = 502
        raise HTTPException(
            status_code=status,
            detail=_provider_error_detail(exc),
        ) from exc
    except APIConnectionError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"无法连接模型服务: {exc.message}",
        ) from exc
    except OpenAIError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc.message) if hasattr(exc, "message") else str(exc),
        ) from exc

    messages = result.get("messages", [])
    last = messages[-1] if messages else None
    text = _strip_reasoning_blocks(
        _message_content_to_text(getattr(last, "content", "") if last else "")
    )
    text = _append_chart_urls(text, _extract_chart_urls(messages))

    if body.debug:
        return ChatResponse(
            reply=text,
            trace=extract_agent_trace(messages, max_preview_chars=body.trace_max_chars),
            execution_trace=execution_trace_from_state({**execution_context, **result}),
            **_result_metadata(result),
        )
    return ChatResponse(reply=text, **_result_metadata(result))


@router.post(
    "/chat/permission",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def approve_permission(
    request: Request,
    body: PermissionApprovalRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ChatResponse:
    """Approve one exact step/tool permission and resume its checkpoint."""
    optional_authenticated_user_or_static_auth(request, authorization)
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": body.thread_id}}
    snapshot = await graph.aget_state(config)
    state = snapshot.values or {}
    pending = state.get("pending_permission") if state else None
    if state.get("status") != "waiting_confirmation" or not isinstance(pending, dict):
        raise HTTPException(status_code=409, detail="当前线程没有待审批操作")
    if (
        pending.get("step_id") != body.step_id
        or pending.get("tool_name") != body.tool_name
    ):
        raise HTTPException(status_code=409, detail="审批对象与当前待审批操作不匹配")

    if body.decision == "cancel":
        await graph.aupdate_state(
            config,
            {
                "status": "failed",
                "failure_reason": "用户已取消执行",
                "pending_permission": None,
                "permission_action": "DENY",
            },
        )
        return ChatResponse(reply="已取消该操作，未执行对应工具。")

    approval_key = f"{body.step_id}:{body.tool_name}"
    approved = list(state.get("approved_permission_keys", []))
    if approval_key not in approved:
        approved.append(approval_key)
    await graph.aupdate_state(
        config,
        {
            "approved_permission_keys": approved,
            "pending_permission": None,
            "permission_action": "ALLOW",
            "status": "executing",
            "failure_reason": None,
        },
        as_node="select_step",
    )
    result = await graph.ainvoke(None, config=config)
    if result.get("status") == "waiting_confirmation" and isinstance(
        result.get("pending_permission"), dict
    ):
        return ChatResponse(reply="", permission_required=result["pending_permission"])
    if result.get("status") == "blocked_missing_state" and isinstance(
        result.get("state_validation"), dict
    ):
        return ChatResponse(
            reply="", clarification_required=result["state_validation"]
        )
    messages = result.get("messages", [])
    last = messages[-1] if messages else None
    text = _strip_reasoning_blocks(
        _message_content_to_text(getattr(last, "content", "") if last else "")
    )
    text = _append_chart_urls(text, _extract_chart_urls(messages))
    return ChatResponse(reply=text, **_result_metadata(result))


@router.post(
    "/chat/clarification",
    response_model=ChatResponse,
    response_model_exclude_none=True,
)
async def submit_clarification(
    request: Request,
    body: ClarificationRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ChatResponse:
    """Store missing state for one blocked step and resume validation."""
    optional_authenticated_user_or_static_auth(request, authorization)
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": body.thread_id}}
    snapshot = await graph.aget_state(config)
    state = snapshot.values or {}
    validation = state.get("state_validation")
    if state.get("status") != "blocked_missing_state" or not isinstance(
        validation, dict
    ):
        raise HTTPException(status_code=409, detail="当前线程没有待补充的信息")
    if validation.get("step_id") != body.step_id:
        raise HTTPException(status_code=409, detail="补充信息与当前阻塞步骤不匹配")

    if body.decision == "cancel":
        await graph.aupdate_state(
            config,
            {"status": "failed", "failure_reason": "用户已取消补充信息"},
        )
        return ChatResponse(reply="已取消补充信息，当前步骤未执行。")
    if not body.values:
        raise HTTPException(status_code=422, detail="请提供至少一个待补充字段")

    clarified = {**state.get("clarified_state", {}), **body.values}
    await graph.aupdate_state(
        config,
        {
            "clarified_state": clarified,
            "status": "executing",
            "failure_reason": None,
        },
        as_node="permission_gate",
    )
    result = await graph.ainvoke(None, config=config)
    if result.get("status") == "blocked_missing_state" and isinstance(
        result.get("state_validation"), dict
    ):
        return ChatResponse(
            reply="", clarification_required=result["state_validation"]
        )
    messages = result.get("messages", [])
    last = messages[-1] if messages else None
    text = _strip_reasoning_blocks(
        _message_content_to_text(getattr(last, "content", "") if last else "")
    )
    text = _append_chart_urls(text, _extract_chart_urls(messages))
    return ChatResponse(reply=text, **_result_metadata(result))


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    body: ChatRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> StreamingResponse:
    """Stream graph progress and final-answer tokens as newline-delimited JSON."""
    current_user = optional_authenticated_user_or_static_auth(request, authorization)
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": body.thread_id}}
    input_messages = _build_input_messages(request, body, current_user)
    execution_context = new_execution_context(body.thread_id)
    user_context = {"user_id": current_user.user_id} if current_user else {}

    async def events() -> AsyncIterator[bytes]:
        messages: list[Any] = list(input_messages)
        streamed_reply = ""
        buffer_for_review = False
        awaiting_permission = False
        permission_halted = False
        awaiting_clarification = False
        final_metadata: dict[str, Any] = {}
        emitted_tool_events: set[tuple[str, int]] = set()
        stripper = _IncrementalReasoningStripper()
        yield _ndjson_event("status", content="正在思考...")

        try:
            async for mode, data in graph.astream(
                {"messages": input_messages, "requested_mode": body.planning, **execution_context, **user_context},
                config=config,
                stream_mode=["messages", "updates"],
            ):
                if mode == "messages":
                    chunk, metadata = data
                    if (
                        metadata.get("langgraph_node") != "compose_answer"
                        or not isinstance(chunk, AIMessageChunk)
                    ):
                        continue
                    if buffer_for_review:
                        continue
                    delta = stripper.feed(
                        _message_content_to_text(getattr(chunk, "content", ""))
                    )
                    if delta:
                        streamed_reply += delta
                        yield _ndjson_event("delta", content=delta)
                    continue

                if mode != "updates" or not isinstance(data, dict):
                    continue
                for node_name, update in data.items():
                    if isinstance(update, dict):
                        for key in (
                            "status", "failure_reason", "last_tool_errors",
                            "permission_failure", "node_events", "tool_events",
                            "decision_events", "error_events", "request_summary",
                        ):
                            if key in update:
                                final_metadata[key] = update[key]
                        node_messages = update.get("messages", [])
                        if not isinstance(node_messages, list):
                            node_messages = [node_messages]
                        messages.extend(message for message in node_messages if message is not None)
                        node_events = update.get("node_events", [])
                        if isinstance(node_events, list):
                            matching_events = [
                                event for event in node_events
                                if isinstance(event, dict) and event.get("node_name") == node_name
                            ]
                            if matching_events:
                                yield _ndjson_event("node_completed", **matching_events[-1])
                    if node_name == "router" and isinstance(update, dict):
                        yield _ndjson_event(
                            "route_selected",
                            mode=update.get("execution_mode", "planned"),
                            source=update.get("route_source", "fallback"),
                            reason=update.get("route_reason", ""),
                        )
                    elif node_name == "prepare_request" and isinstance(update, dict):
                        if update.get("route_action") in {"resume", "cancel"}:
                            yield _ndjson_event(
                                "route_selected",
                                mode="planned",
                                source="resume",
                                reason=update.get("route_reason", ""),
                            )
                    elif node_name == "planner" and isinstance(update, dict):
                        plan = update.get("plan")
                        if isinstance(plan, dict):
                            yield _ndjson_event(
                                "plan_created",
                                goal=plan.get("goal", ""),
                                steps=plan.get("steps", []),
                            )
                    elif node_name == "replan" and isinstance(update, dict):
                        plan = update.get("plan")
                        if isinstance(plan, dict):
                            yield _ndjson_event(
                                "plan_updated",
                                goal=plan.get("goal", ""),
                                steps=plan.get("steps", []),
                            )
                    elif node_name == "select_step" and isinstance(update, dict):
                        step_id = update.get("current_step_id")
                        if step_id and update.get("status") == "executing":
                            yield _ndjson_event("step_started", step_id=step_id)
                    elif node_name == "permission_gate" and isinstance(update, dict):
                        pending = update.get("pending_permission")
                        yield _ndjson_event(
                            "permission_checked",
                            trace_id=execution_context["trace_id"],
                            action=str(update.get("permission_action", "")).lower(),
                            risk_level=(pending or {}).get("risk_level", "none"),
                            reason=(pending or {}).get("reason", ""),
                        )
                        if update.get("permission_action") == "NEED_CONFIRM" and isinstance(
                            pending, dict
                        ):
                            awaiting_permission = True
                            yield _ndjson_event("permission_required", **pending)
                        elif update.get("permission_action") == "DENY" and isinstance(
                            pending, dict
                        ):
                            permission_halted = True
                            yield _ndjson_event(
                                "error",
                                message=pending.get("reason", "操作已被权限策略拒绝"),
                            )
                    elif node_name == "state_validation" and isinstance(update, dict):
                        validation = update.get("state_validation")
                        if (
                            update.get("state_validation_action") == "MISSING"
                            and update.get("block_resolution") == "clarification"
                            and isinstance(validation, dict)
                        ):
                            awaiting_clarification = True
                            yield _ndjson_event("clarification_required", **validation)
                    elif node_name == "evaluator" and isinstance(update, dict):
                        action = update.get("evaluation_action")
                        yield _ndjson_event(
                            "step_evaluated",
                            step_id=update.get("current_step_id"),
                            action=action,
                        )
                        if action in {"pass", "partial", "fail"}:
                            yield _ndjson_event(
                                "step_completed",
                                step_id=update.get("current_step_id"),
                                status=("success" if action == "pass" else action),
                            )
                    elif node_name == "review_gate" and isinstance(update, dict):
                        buffer_for_review = bool(update.get("review_required"))
                        yield _ndjson_event(
                            "review_decided",
                            required=buffer_for_review,
                            reason=update.get("review_reason", ""),
                        )
                    elif node_name == "reviewer" and isinstance(update, dict):
                        yield _ndjson_event(
                            "answer_reviewed",
                            action=update.get("review_action"),
                        )
                    elif node_name == "tools":
                        if isinstance(update, dict):
                            current_events = update.get("tool_events", [])
                            for event in current_events:
                                event_key = (str(event.get("tool_call_id", "")), int(event.get("attempt", 0)))
                                if event_key in emitted_tool_events:
                                    continue
                                emitted_tool_events.add(event_key)
                                if event.get("attempt", 1) > 1:
                                    yield _ndjson_event("tool_retry", **event)
                                if event.get("recovered"):
                                    yield _ndjson_event("tool_recovered", **event)
                        yield _ndjson_event("status", content="工具执行完成，正在整理结果...")
                    elif node_name == "execution_completed" and isinstance(update, dict):
                        yield _ndjson_event(
                            "execution_completed",
                            **(update.get("request_summary") or {}),
                        )
                    elif node_name in {"executor", "direct_agent"}:
                        yield _ndjson_event("status", content="正在分析执行结果...")
                    elif node_name == "budget_exceeded":
                        yield _ndjson_event(
                            "status", content="执行预算已达到上限，正在整理部分结果..."
                        )

            if awaiting_permission or permission_halted or awaiting_clarification:
                return

            tail = stripper.feed("", final=True)
            if tail:
                streamed_reply += tail
                yield _ndjson_event("delta", content=tail)

            last = messages[-1] if messages else None
            reply = _strip_reasoning_blocks(
                _message_content_to_text(getattr(last, "content", "") if last else "")
            )
            reply = _append_chart_urls(reply or streamed_reply, _extract_chart_urls(messages))
            if buffer_for_review and reply:
                streamed_reply = reply
                yield _ndjson_event("delta", content=reply)
            payload: dict[str, Any] = {
                "reply": reply,
                **{
                    key: value
                    for key, value in _result_metadata(final_metadata).items()
                    if value is not None
                },
            }
            if body.debug:
                payload["trace"] = [
                    event.model_dump(exclude_none=True)
                    for event in extract_agent_trace(
                        messages, max_preview_chars=body.trace_max_chars
                    )
                ]
                payload["execution_trace"] = execution_trace_from_state(
                    {**execution_context, **final_metadata}
                )
            yield _ndjson_event("done", **payload)
        except APIStatusError as exc:
            yield _ndjson_event("error", message=_provider_error_detail(exc))
        except APIConnectionError as exc:
            yield _ndjson_event("error", message=f"无法连接模型服务: {exc.message}")
        except OpenAIError as exc:
            message = str(exc.message) if hasattr(exc, "message") else str(exc)
            yield _ndjson_event("error", message=message)
        except Exception as exc:  # stream headers have already been sent
            yield _ndjson_event("error", message=str(exc))

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
