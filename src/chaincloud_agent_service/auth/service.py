"""Authentication service for the PostgreSQL-backed login MVP."""

from __future__ import annotations

from typing import Any

from chaincloud_agent_service.auth.password import hash_password, verify_password
from chaincloud_agent_service.auth.store import (
    UserRecord,
    UserStore,
    normalize_username,
)
from chaincloud_agent_service.auth.tokens import create_token, verify_token


class AuthConflictError(ValueError):
    pass


class AuthInvalidCredentialsError(ValueError):
    pass


class AuthInvalidTokenError(ValueError):
    pass


class AuthService:
    def __init__(
        self,
        user_store: UserStore,
        *,
        token_secret: str,
        token_expire_minutes: int,
    ) -> None:
        self.user_store = user_store
        self.token_secret = token_secret
        self.token_expire_minutes = token_expire_minutes

    def register(
        self,
        *,
        username: str,
        password: str,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UserRecord:
        username = normalize_username(username)
        if self.user_store.get_user_by_username(username) is not None:
            raise AuthConflictError("username already exists")

        try:
            return self.user_store.create_user(
                username=username,
                password_hash=hash_password(password),
                display_name=display_name,
                metadata=metadata or {},
            )
        except ValueError as exc:
            raise AuthConflictError(str(exc)) from exc

    def login(self, *, username: str, password: str) -> tuple[str, UserRecord]:
        user = self.user_store.get_user_by_username(username)
        if user is None or not verify_password(password, user.password_hash):
            raise AuthInvalidCredentialsError("invalid username or password")

        updated = self.user_store.update_last_login(user.user_id)
        user = updated or user
        token = create_token(
            user_id=user.user_id,
            username=user.username,
            secret=self.token_secret,
            expire_minutes=self.token_expire_minutes,
        )
        return token, user

    def issue_token_for_user(self, user: UserRecord) -> str:
        return create_token(
            user_id=user.user_id,
            username=user.username,
            secret=self.token_secret,
            expire_minutes=self.token_expire_minutes,
        )

    def authenticate_token(self, token: str) -> UserRecord:
        payload = verify_token(token, secret=self.token_secret)
        if payload is None:
            raise AuthInvalidTokenError("invalid or expired token")

        user_id = str(payload.get("sub") or "")
        if not user_id:
            raise AuthInvalidTokenError("invalid token payload")

        user = self.user_store.get_user_by_id(user_id)
        if user is None:
            raise AuthInvalidTokenError("user not found")
        return user
