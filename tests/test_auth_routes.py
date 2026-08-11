from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaincloud_agent_service.api.routes.auth import router
from chaincloud_agent_service.auth.service import AuthService
from chaincloud_agent_service.auth.store import InMemoryUserStore


def _client() -> TestClient:
    app = FastAPI()
    app.state.auth_service = AuthService(
        InMemoryUserStore(),
        token_secret="test-secret",
        token_expire_minutes=60,
    )
    app.include_router(router)
    return TestClient(app)


def test_register_login_and_me_flow() -> None:
    client = _client()

    register_response = client.post(
        "/auth/register",
        json={
            "username": "Alice",
            "password": "secret-password",
            "display_name": "Alice",
        },
    )
    assert register_response.status_code == 200
    register_payload = register_response.json()
    assert register_payload["access_token"]
    assert register_payload["token_type"] == "bearer"
    assert register_payload["user"]["username"] == "alice"

    login_response = client.post(
        "/auth/login",
        json={
            "username": "alice",
            "password": "secret-password",
        },
    )
    assert login_response.status_code == 200
    login_payload = login_response.json()
    token = login_payload["access_token"]

    me_response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "alice"


def test_register_duplicate_username_returns_409() -> None:
    client = _client()

    body = {"username": "bob", "password": "secret-password"}
    assert client.post("/auth/register", json=body).status_code == 200
    assert client.post("/auth/register", json=body).status_code == 409


def test_login_rejects_wrong_password() -> None:
    client = _client()

    client.post(
        "/auth/register",
        json={"username": "carol", "password": "secret-password"},
    )

    response = client.post(
        "/auth/login",
        json={"username": "carol", "password": "wrong-password"},
    )

    assert response.status_code == 401


def test_me_requires_bearer_token() -> None:
    client = _client()

    response = client.get("/auth/me")

    assert response.status_code == 401
