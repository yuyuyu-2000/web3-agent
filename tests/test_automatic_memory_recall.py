from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, SystemMessage

from chaincloud_agent_service.api.routes import chat as chat_routes
from chaincloud_agent_service.auth import AuthService, InMemoryUserStore
from chaincloud_agent_service.memory import InMemoryMemoryStore, MemoryService


class Embeddings:
    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0] if "ChainCloud" in text or "继续" in text else [0.0, 1.0]


class Graph:
    last_payload = None
    state: dict = {}

    async def aget_state(self, config):  # type: ignore[no-untyped-def]
        return SimpleNamespace(values=self.state)

    async def ainvoke(self, payload, config=None):  # type: ignore[no-untyped-def]
        self.last_payload = payload
        return {**payload, "messages": [*payload["messages"], AIMessage(content="ok")]}


def client_and_user():  # type: ignore[no-untyped-def]
    app = FastAPI()
    graph = Graph()
    service = MemoryService(InMemoryMemoryStore(), Embeddings())
    auth = AuthService(
        InMemoryUserStore(), token_secret="secret", token_expire_minutes=60
    )
    user = auth.register(username="alice", password="password123")
    app.state.graph = graph
    app.state.memory_service = service
    app.state.auth_service = auth
    app.state.settings = SimpleNamespace(
        chat_api_token=None,
        memory_recall_enabled=True,
        memory_recall_min_similarity=0.72,
        memory_recall_candidate_limit=5,
        memory_recall_selected_limit=3,
    )
    app.include_router(chat_routes.router)
    headers = {"Authorization": f"Bearer {auth.issue_token_for_user(user)}"}
    return TestClient(app), graph, service, user, headers


def test_related_owned_memory_is_recalled_without_memory_key() -> None:
    client, graph, service, user, headers = client_and_user()
    service.save_memory(
        memory_key="project",
        summary="ChainCloud 项目采用测试优先。",
        source_thread_id="old",
        metadata={"user_id": user.user_id},
    )
    response = client.post(
        "/chat",
        headers=headers,
        json={"thread_id": "new", "message": "继续之前的 ChainCloud 工作"},
    )
    assert response.status_code == 200
    assert isinstance(graph.last_payload["messages"][0], SystemMessage)
    assert graph.last_payload["recalled_memory_keys"] == ["project"]


def test_unrelated_memory_is_not_forced_into_top_k_and_users_are_isolated() -> None:
    client, graph, service, user, headers = client_and_user()
    service.save_memory(
        memory_key="other-topic",
        summary="旅游计划",
        source_thread_id="old",
        metadata={"user_id": user.user_id},
    )
    service.save_memory(
        memory_key="other-user",
        summary="ChainCloud 私密项目",
        source_thread_id="old",
        metadata={"user_id": "bob"},
    )
    client.post(
        "/chat",
        headers=headers,
        json={"thread_id": "new", "message": "继续之前的 ChainCloud 工作"},
    )
    assert [message.type for message in graph.last_payload["messages"]] == ["human"]
    assert graph.last_payload["recalled_memory_keys"] == []


def test_current_fact_request_takes_cheap_path() -> None:
    client, graph, service, user, headers = client_and_user()
    service.save_memory(
        memory_key="project",
        summary="ChainCloud 项目",
        source_thread_id="old",
        metadata={"user_id": user.user_id},
    )
    client.post(
        "/chat",
        headers=headers,
        json={"thread_id": "new", "message": "查一下今天 ETH gas"},
    )
    event = graph.last_payload["memory_recall_events"][0]
    assert event["memory_recall_triggered"] is False
    assert event["memory_recall_skipped_reason"] == "current_or_independent_request"


def test_embedding_failure_degrades_to_no_memory() -> None:
    client, graph, service, _, headers = client_and_user()

    class Broken:
        def embed_query(self, text):  # type: ignore[no-untyped-def]
            raise RuntimeError("down")

    service.embedding_provider = Broken()
    response = client.post(
        "/chat", headers=headers, json={"thread_id": "new", "message": "继续之前的工作"}
    )
    assert response.status_code == 200
    assert [message.type for message in graph.last_payload["messages"]] == ["human"]


def test_checkpoint_reuse_is_also_user_isolated() -> None:
    client, graph, _, _, headers = client_and_user()
    graph.state = {
        "memory_recall_query": "继续 ChainCloud",
        "active_recalled_memories": [
            {
                "memory_key": "bob-private",
                "summary": "Bob 私有内容",
                "metadata": {"user_id": "bob"},
                "user_id": "bob",
            }
        ],
    }
    response = client.post(
        "/chat",
        headers=headers,
        json={"thread_id": "shared", "message": "继续之前的 ChainCloud 工作"},
    )
    assert response.status_code == 200
    assert [message.type for message in graph.last_payload["messages"]] == ["human"]
