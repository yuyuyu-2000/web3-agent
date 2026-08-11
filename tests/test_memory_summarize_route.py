from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from chaincloud_agent_service.api.routes import memory as memory_routes
from chaincloud_agent_service.auth import AuthService, InMemoryUserStore
from chaincloud_agent_service.memory import InMemoryMemoryStore, MemoryService


class FakeGraph:
    def __init__(self, messages=None):
        self.messages = messages or []
        self.last_config = None

    async def aget_state(self, config):  # type: ignore[no-untyped-def]
        self.last_config = config
        return SimpleNamespace(values={"messages": self.messages})


class FakeLLM:
    def __init__(self) -> None:
        self.last_messages = None

    async def ainvoke(self, messages):  # type: ignore[no-untyped-def]
        self.last_messages = messages
        return AIMessage(content="用户正在推进 ChainCloud Memory v1 的自动摘要接口。")


def make_client(
    *, graph: FakeGraph | None = None, llm: FakeLLM | None = None
) -> tuple[TestClient, AuthService]:
    app = FastAPI()
    auth_service = AuthService(
        InMemoryUserStore(), token_secret="test-secret", token_expire_minutes=60
    )
    app.state.settings = SimpleNamespace(chat_api_token=None)
    app.state.auth_service = auth_service
    app.state.memory_service = MemoryService(InMemoryMemoryStore())
    app.state.graph = graph or FakeGraph(
        messages=[
            HumanMessage(content="我们继续做 /memory/summarize 接口。"),
            AIMessage(content="可以从 checkpoint 读取 thread messages。"),
        ]
    )
    app.state.memory_llm = llm or FakeLLM()
    app.include_router(memory_routes.router)
    return TestClient(app), auth_service


def auth_header(auth_service: AuthService) -> dict[str, str]:
    user = auth_service.register(username="alice", password="password123")
    return {"Authorization": f"Bearer {auth_service.issue_token_for_user(user)}"}


def test_summarize_memory_from_thread_messages() -> None:
    graph = FakeGraph(
        messages=[
            HumanMessage(content="我们继续做 /memory/summarize 接口。"),
            AIMessage(content="可以从 checkpoint 读取 thread messages。"),
        ]
    )
    llm = FakeLLM()
    client, auth_service = make_client(graph=graph, llm=llm)
    headers = auth_header(auth_service)
    response = client.post(
        "/memory/summarize",
        headers=headers,
        json={
            "thread_id": "thread-1",
            "memory_key": "alice-memory",
            "metadata": {"stage": "summarize-route"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["memory_key"] == "alice-memory"
    assert body["metadata"]["stage"] == "summarize-route"
    assert body["metadata"]["summary_source"] == "thread_checkpoint"
    assert body["metadata"]["username"] == "alice"
    assert "user_id" in body["metadata"]
    assert graph.last_config == {"configurable": {"thread_id": "thread-1"}}
    assert llm.last_messages is not None
    assert "/memory/summarize" in str(llm.last_messages[0].content)
    fetched = client.get("/memory/alice-memory", headers=headers)
    assert fetched.status_code == 200


def test_summarize_memory_returns_404_when_thread_has_no_messages() -> None:
    client, auth_service = make_client(graph=FakeGraph(messages=[]))
    response = client.post(
        "/memory/summarize",
        headers=auth_header(auth_service),
        json={"thread_id": "missing-thread", "memory_key": "missing-memory"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "thread messages not found"


def test_summarize_memory_requires_user_bearer_auth() -> None:
    client, _ = make_client()
    assert (
        client.post(
            "/memory/summarize",
            json={"thread_id": "thread-1", "memory_key": "chaincloud-memory"},
        ).status_code
        == 401
    )
