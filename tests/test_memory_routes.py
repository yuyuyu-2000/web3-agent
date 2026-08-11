from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaincloud_agent_service.api.routes import memory as memory_routes
from chaincloud_agent_service.auth import AuthService, InMemoryUserStore
from chaincloud_agent_service.memory import InMemoryMemoryStore, MemoryService


def make_client() -> tuple[TestClient, AuthService, MemoryService]:
    app = FastAPI()
    memory_service = MemoryService(InMemoryMemoryStore())
    auth_service = AuthService(
        InMemoryUserStore(), token_secret="test-secret", token_expire_minutes=60
    )
    app.state.settings = SimpleNamespace(chat_api_token=None)
    app.state.auth_service = auth_service
    app.state.memory_service = memory_service
    app.include_router(memory_routes.router)
    return TestClient(app), auth_service, memory_service


def auth_header(auth_service: AuthService, username: str = "alice") -> dict[str, str]:
    user = auth_service.register(username=username, password="password123")
    return {"Authorization": f"Bearer {auth_service.issue_token_for_user(user)}"}


def test_save_and_get_memory_via_http() -> None:
    client, auth_service, _ = make_client()
    headers = auth_header(auth_service)
    created = client.post(
        "/memory",
        headers=headers,
        json={
            "memory_key": "alice-memory",
            "summary": "用户正在重构 ChainCloud Memory v1。",
            "source_thread_id": "thread-1",
            "metadata": {"stage": "routes"},
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["memory_key"] == "alice-memory"
    assert body["metadata"]["stage"] == "routes"
    assert body["metadata"]["username"] == "alice"
    assert "user_id" in body["metadata"]
    fetched = client.get("/memory/alice-memory", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["memory_key"] == "alice-memory"


def test_list_memories_is_filtered_by_authenticated_user() -> None:
    client, auth_service, memory_service = make_client()
    alice = auth_service.register(username="alice", password="password123")
    bob = auth_service.register(username="bob", password="password123")
    alice_headers = {
        "Authorization": f"Bearer {auth_service.issue_token_for_user(alice)}"
    }
    bob_headers = {"Authorization": f"Bearer {auth_service.issue_token_for_user(bob)}"}
    memory_service.save_memory(
        memory_key="alice-memory",
        summary="Alice 摘要",
        source_thread_id="thread-a",
        metadata={"user_id": alice.user_id, "username": alice.username},
    )
    memory_service.save_memory(
        memory_key="bob-memory",
        summary="Bob 摘要",
        source_thread_id="thread-b",
        metadata={"user_id": bob.user_id, "username": bob.username},
    )
    memory_service.save_memory(
        memory_key="legacy-memory",
        summary="无用户元数据旧摘要",
        source_thread_id="thread-legacy",
    )
    alice_response = client.get("/memory", headers=alice_headers)
    bob_response = client.get("/memory", headers=bob_headers)
    assert alice_response.status_code == 200
    assert {item["memory_key"] for item in alice_response.json()["memories"]} == {
        "alice-memory"
    }
    assert bob_response.status_code == 200
    assert {item["memory_key"] for item in bob_response.json()["memories"]} == {
        "bob-memory"
    }


def test_user_cannot_read_or_delete_other_users_memory() -> None:
    client, auth_service, memory_service = make_client()
    alice = auth_service.register(username="alice", password="password123")
    bob = auth_service.register(username="bob", password="password123")
    alice_headers = {
        "Authorization": f"Bearer {auth_service.issue_token_for_user(alice)}"
    }
    bob_headers = {"Authorization": f"Bearer {auth_service.issue_token_for_user(bob)}"}
    memory_service.save_memory(
        memory_key="alice-memory",
        summary="Alice 摘要",
        source_thread_id="thread-a",
        metadata={"user_id": alice.user_id, "username": alice.username},
    )
    assert client.get("/memory/alice-memory", headers=bob_headers).status_code == 404
    assert client.delete("/memory/alice-memory", headers=bob_headers).status_code == 404
    assert (
        client.delete("/memory/alice-memory", headers=alice_headers).status_code == 204
    )


def test_memory_routes_require_user_bearer_auth() -> None:
    client, _, _ = make_client()
    assert client.get("/memory").status_code == 401
