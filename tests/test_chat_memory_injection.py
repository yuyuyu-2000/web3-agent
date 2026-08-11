from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage

from chaincloud_agent_service.api.routes import chat as chat_routes
from chaincloud_agent_service.auth import AuthService, InMemoryUserStore
from chaincloud_agent_service.memory import InMemoryMemoryStore, MemoryService


class FakeGraph:
    def __init__(self) -> None:
        self.last_payload = None
        self.last_config = None

    async def ainvoke(self, payload, config=None):  # type: ignore[no-untyped-def]
        self.last_payload = payload
        self.last_config = config
        return {"messages": [AIMessage(content="已根据上下文完成回答。")]}

    async def astream(  # type: ignore[no-untyped-def]
        self, payload, config=None, stream_mode=None
    ):
        self.last_payload = payload
        self.last_config = config
        yield (
            "updates",
            {
                "router": {
                    "execution_mode": "direct",
                    "route_source": "rule",
                    "route_reason": "测试直接路径",
                }
            },
        )
        yield (
            "messages",
            (AIMessageChunk(content="已根据"), {"langgraph_node": "compose_answer"}),
        )
        yield (
            "messages",
            (AIMessageChunk(content="上下文完成回答。"), {"langgraph_node": "compose_answer"}),
        )
        # LangGraph also emits the node's aggregated return value in messages mode.
        # It must not be forwarded as another delta.
        yield (
            "messages",
            (AIMessage(content="已根据上下文完成回答。"), {"langgraph_node": "compose_answer"}),
        )
        yield (
            "updates",
            {
                "compose_answer": {
                    "messages": [AIMessage(content="已根据上下文完成回答。")]
                }
            },
        )


def make_client(
    *, chat_api_token: str | None = None
) -> tuple[TestClient, FakeGraph, MemoryService, AuthService]:
    app = FastAPI()
    graph = FakeGraph()
    memory_service = MemoryService(InMemoryMemoryStore())
    auth_service = AuthService(
        InMemoryUserStore(), token_secret="test-secret", token_expire_minutes=60
    )
    app.state.settings = SimpleNamespace(chat_api_token=chat_api_token)
    app.state.graph = graph
    app.state.memory_service = memory_service
    app.state.auth_service = auth_service
    app.include_router(chat_routes.router)
    return TestClient(app), graph, memory_service, auth_service


def auth_header(
    auth_service: AuthService, username: str = "alice"
) -> tuple[dict[str, str], str, str]:
    user = auth_service.register(username=username, password="password123")
    return (
        {"Authorization": f"Bearer {auth_service.issue_token_for_user(user)}"},
        user.user_id,
        user.username,
    )


def test_chat_without_memory_key_keeps_normal_behavior() -> None:
    client, graph, _, _ = make_client()
    response = client.post("/chat", json={"thread_id": "thread-1", "message": "你好"})
    assert response.status_code == 200
    assert response.json() == {"reply": "已根据上下文完成回答。"}
    messages = graph.last_payload["messages"]
    assert len(messages) == 1
    assert messages[0].content == "你好"
    assert graph.last_payload["requested_mode"] == "auto"
    assert graph.last_config == {"configurable": {"thread_id": "thread-1"}}


def test_chat_accepts_explicit_planning_mode() -> None:
    client, graph, _, _ = make_client()

    response = client.post(
        "/chat",
        json={"thread_id": "thread-planned", "message": "分析地址", "planning": "planned"},
    )

    assert response.status_code == 200
    assert graph.last_payload["requested_mode"] == "planned"


def test_chat_rejects_invalid_planning_mode() -> None:
    client, _, _, _ = make_client()

    response = client.post(
        "/chat",
        json={"thread_id": "thread-invalid", "message": "你好", "planning": "invalid"},
    )

    assert response.status_code == 422


def test_chat_stream_returns_status_deltas_and_done_event() -> None:
    client, graph, _, _ = make_client()
    response = client.post(
        "/chat/stream", json={"thread_id": "thread-stream", "message": "你好"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[0] == {"type": "status", "content": "正在思考..."}
    assert {
        "type": "route_selected",
        "mode": "direct",
        "source": "rule",
        "reason": "测试直接路径",
    } in events
    assert "".join(
        event["content"] for event in events if event["type"] == "delta"
    ) == "已根据上下文完成回答。"
    assert events[-1] == {"type": "done", "reply": "已根据上下文完成回答。"}
    assert graph.last_config == {"configurable": {"thread_id": "thread-stream"}}


def test_chat_stream_does_not_emit_unreviewed_draft() -> None:
    class ReviewedFakeGraph:
        async def astream(self, payload, config=None, stream_mode=None):  # type: ignore[no-untyped-def]
            yield (
                "updates",
                {
                    "review_gate": {
                        "review_required": True,
                        "review_reason": "高风险 Direct 回答",
                    }
                },
            )
            yield (
                "messages",
                (AIMessageChunk(content="未经审查的草稿"), {"langgraph_node": "compose_answer"}),
            )
            yield (
                "updates",
                {"compose_answer": {"messages": [AIMessage(content="未经审查的草稿")]}},
            )
            yield (
                "updates",
                {"reviewer": {"review_action": "revise"}},
            )
            yield (
                "messages",
                (AIMessageChunk(content="审查后的答案"), {"langgraph_node": "compose_answer"}),
            )
            yield (
                "updates",
                {"compose_answer": {"messages": [AIMessage(content="审查后的答案")]}},
            )
            yield (
                "updates",
                {"reviewer": {"review_action": "approve"}},
            )

    client, _, _, _ = make_client()
    client.app.state.graph = ReviewedFakeGraph()

    response = client.post(
        "/chat/stream",
        json={"thread_id": "reviewed-stream", "message": "分析清算风险"},
    )

    events = [json.loads(line) for line in response.text.splitlines()]
    deltas = [event["content"] for event in events if event["type"] == "delta"]
    assert deltas == ["审查后的答案"]
    assert "未经审查的草稿" not in response.text
    assert events[-1] == {"type": "done", "reply": "审查后的答案"}


def test_chat_with_memory_key_injects_owned_system_message() -> None:
    client, graph, memory_service, auth_service = make_client()
    headers, user_id, username = auth_header(auth_service)
    memory_service.save_memory(
        memory_key="alice-memory",
        summary="用户正在按小步提交、测试优先的方式重构 ChainCloud Memory v1。",
        source_thread_id="source-thread",
        metadata={"user_id": user_id, "username": username},
    )
    response = client.post(
        "/chat",
        headers=headers,
        json={
            "thread_id": "thread-2",
            "message": "继续下一步",
            "memory_key": "alice-memory",
        },
    )
    assert response.status_code == 200
    messages = graph.last_payload["messages"]
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert "ChainCloud Memory v1" in str(messages[0].content)
    assert messages[1].content == "继续下一步"


def test_chat_cannot_inject_other_users_memory() -> None:
    client, _, memory_service, auth_service = make_client()
    alice_headers, alice_user_id, alice_username = auth_header(auth_service, "alice")
    bob_headers, _, _ = auth_header(auth_service, "bob")
    memory_service.save_memory(
        memory_key="alice-memory",
        summary="Alice 私有摘要。",
        source_thread_id="source-thread",
        metadata={"user_id": alice_user_id, "username": alice_username},
    )
    forbidden = client.post(
        "/chat",
        headers=bob_headers,
        json={
            "thread_id": "thread-2",
            "message": "继续下一步",
            "memory_key": "alice-memory",
        },
    )
    allowed = client.post(
        "/chat",
        headers=alice_headers,
        json={
            "thread_id": "thread-2",
            "message": "继续下一步",
            "memory_key": "alice-memory",
        },
    )
    assert forbidden.status_code == 404
    assert allowed.status_code == 200


def test_chat_with_missing_memory_key_returns_404() -> None:
    client, _, _, auth_service = make_client()
    headers, _, _ = auth_header(auth_service)
    response = client.post(
        "/chat",
        headers=headers,
        json={
            "thread_id": "thread-3",
            "message": "继续下一步",
            "memory_key": "missing-memory",
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "memory not found"


def test_chat_still_supports_static_api_token_for_legacy_clients() -> None:
    client, graph, _, _ = make_client(chat_api_token="secret-token")
    response = client.post(
        "/chat",
        headers={"Authorization": "Bearer secret-token"},
        json={"thread_id": "thread-legacy", "message": "你好"},
    )
    assert response.status_code == 200
    assert graph.last_payload["messages"][0].content == "你好"
