"""In-memory state shared by query and visualization tools."""

from __future__ import annotations

_LAST_QUERY_RESULT: list[dict] = []


def set_last_query_result(rows: list[dict]) -> None:
    global _LAST_QUERY_RESULT
    _LAST_QUERY_RESULT = rows


def get_last_query_result() -> list[dict]:
    return _LAST_QUERY_RESULT


def clear_last_query_result() -> None:
    set_last_query_result([])
