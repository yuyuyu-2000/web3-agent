"""User authentication routes for local/PostgreSQL login MVP."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from chaincloud_agent_service.auth.service import (
    AuthConflictError,
    AuthInvalidCredentialsError,
    AuthInvalidTokenError,
    AuthService,
)
from chaincloud_agent_service.auth.store import UserRecord

router = APIRouter(prefix="/auth")


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6, max_length=256)
    display_name: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class UserResponse(BaseModel):
    user_id: str
    username: str
    display_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


def _auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise HTTPException(status_code=500, detail="auth service is not initialized")
    return service


def _to_user_response(user: UserRecord) -> UserResponse:
    return UserResponse(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        metadata=user.metadata,
    )


def _bearer_token(authorization: str | None) -> str:
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


@router.post("/register", response_model=AuthTokenResponse)
async def register(request: Request, body: RegisterRequest) -> AuthTokenResponse:
    service = _auth_service(request)

    try:
        user = service.register(
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            metadata=body.metadata,
        )
    except AuthConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    token = service.issue_token_for_user(user)
    return AuthTokenResponse(
        access_token=token,
        expires_in=service.token_expire_minutes * 60,
        user=_to_user_response(user),
    )


@router.post("/login", response_model=AuthTokenResponse)
async def login(request: Request, body: LoginRequest) -> AuthTokenResponse:
    service = _auth_service(request)

    try:
        token, user = service.login(username=body.username, password=body.password)
    except AuthInvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    return AuthTokenResponse(
        access_token=token,
        expires_in=service.token_expire_minutes * 60,
        user=_to_user_response(user),
    )


@router.get("/me", response_model=UserResponse)
async def me(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> UserResponse:
    service = _auth_service(request)
    token = _bearer_token(authorization)

    try:
        user = service.authenticate_token(token)
    except AuthInvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    return _to_user_response(user)
