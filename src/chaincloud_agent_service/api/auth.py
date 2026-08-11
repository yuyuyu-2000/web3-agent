from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from chaincloud_agent_service.auth.service import AuthInvalidTokenError
from chaincloud_agent_service.auth.store import UserRecord


def check_auth(settings: Any, authorization: str | None) -> None:
    if not getattr(settings, "chat_api_token", None):
        return

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少或无效的 Authorization")

    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.chat_api_token:
        raise HTTPException(status_code=401, detail="凭证无效")


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing authorization header",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authorization header",
        )
    return token.strip()


def require_authenticated_user(
    request: Request, authorization: str | None
) -> UserRecord:
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise HTTPException(status_code=500, detail="auth service is not initialized")

    token = bearer_token(authorization)
    try:
        return service.authenticate_token(token)
    except AuthInvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


def optional_authenticated_user_or_static_auth(
    request: Request,
    authorization: str | None,
) -> UserRecord | None:
    service = getattr(request.app.state, "auth_service", None)
    settings = getattr(request.app.state, "settings", None)

    if authorization and authorization.startswith("Bearer ") and service is not None:
        token = authorization.removeprefix("Bearer ").strip()
        try:
            return service.authenticate_token(token)
        except AuthInvalidTokenError:
            pass

    if settings is not None and getattr(settings, "chat_api_token", None):
        check_auth(settings, authorization)
        return None

    return None
