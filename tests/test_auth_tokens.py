from chaincloud_agent_service.auth.tokens import create_token, verify_token


def test_create_and_verify_token() -> None:
    token = create_token(
        user_id="user-1",
        username="alice",
        secret="test-secret",
        expire_minutes=10,
    )

    payload = verify_token(token, secret="test-secret")

    assert payload is not None
    assert payload["sub"] == "user-1"
    assert payload["username"] == "alice"


def test_verify_token_rejects_wrong_secret() -> None:
    token = create_token(
        user_id="user-1",
        username="alice",
        secret="test-secret",
        expire_minutes=10,
    )

    assert verify_token(token, secret="wrong-secret") is None
