from __future__ import annotations

from contextvars import ContextVar, Token

from chaincloud_agent_service.monitoring.store import MonitorStore

_user_id: ContextVar[str | None] = ContextVar("monitor_user_id", default=None)
_store: MonitorStore | None = None


def configure_monitor_store(store: MonitorStore | None) -> None:
    global _store
    _store = store


def monitor_store() -> MonitorStore:
    if _store is None:
        raise RuntimeError("monitor storage is not configured")
    return _store


def bind_monitor_user(user_id: str | None) -> Token:
    return _user_id.set(user_id)


def reset_monitor_user(token: Token) -> None:
    _user_id.reset(token)


def current_monitor_user() -> str:
    user_id = _user_id.get()
    if not user_id:
        raise PermissionError("monitor tools require an authenticated user")
    return user_id

