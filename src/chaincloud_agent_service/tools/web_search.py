"""Public web search tool for grounding recent events and external facts."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


_MAX_RESPONSE_CHARS = 200_000


class WebSearchInput(BaseModel):
    query: str = Field(
        description=(
            "搜索查询词。适用于近期事件、官方公告、治理论坛、安全报告、"
            "新闻事实核验、攻击归因、资金流公开进展等。"
        )
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=10,
        description="返回结果数量，范围 1 到 10。",
    )


def _truncate_text(value: Any, max_chars: int = 1200) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_chars:
        return text[:max_chars] + "...(truncated)"
    return text


def _normalize_tavily_result(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize Tavily search result fields for stable tool output."""
    return {
        "title": _truncate_text(item.get("title"), 300),
        "url": _truncate_text(item.get("url"), 800),
        "content": _truncate_text(item.get("content") or item.get("snippet"), 1500),
        "score": item.get("score"),
        "published_date": item.get("published_date"),
    }


def _post_tavily_search(
    *,
    api_key: str,
    query: str,
    max_results: int,
    timeout_sec: int,
) -> str:
    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_answer": False,
        "include_raw_content": False,
    }

    raw_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = Request(
        "https://api.tavily.com/search",
        data=raw_payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    with urlopen(request, timeout=timeout_sec) as response:
        raw = response.read().decode("utf-8", errors="replace")

    if len(raw) > _MAX_RESPONSE_CHARS:
        raw = raw[:_MAX_RESPONSE_CHARS] + "\n... (响应已截断)"

    data = json.loads(raw)
    raw_results = data.get("results", [])
    results = [
        _normalize_tavily_result(item) for item in raw_results if isinstance(item, dict)
    ]

    return json.dumps(
        {
            "provider": "tavily",
            "query": query,
            "count": len(results),
            "results": results,
        },
        ensure_ascii=False,
        default=str,
    )


def make_web_search_tool(
    *,
    provider: str,
    tavily_api_key: str | None,
    default_max_results: int = 5,
    timeout_sec: int = 15,
) -> StructuredTool:
    """Create a web_search tool.

    The first implementation supports Tavily. The provider parameter is kept
    so the tool can be extended to other search providers later.
    """

    def _invoke(query: str, max_results: int = default_max_results) -> str:
        cleaned_query = query.strip()
        if not cleaned_query:
            return json.dumps(
                {
                    "provider": provider,
                    "query": query,
                    "count": 0,
                    "results": [],
                    "error": "query 不能为空",
                },
                ensure_ascii=False,
            )

        safe_max_results = max(1, min(int(max_results or default_max_results), 10))

        if provider != "tavily":
            return json.dumps(
                {
                    "provider": provider,
                    "query": cleaned_query,
                    "count": 0,
                    "results": [],
                    "error": f"暂不支持的 WEB_SEARCH_PROVIDER: {provider}",
                },
                ensure_ascii=False,
            )

        if not tavily_api_key:
            return json.dumps(
                {
                    "provider": "tavily",
                    "query": cleaned_query,
                    "count": 0,
                    "results": [],
                    "error": "未配置 TAVILY_API_KEY",
                },
                ensure_ascii=False,
            )

        try:
            return _post_tavily_search(
                api_key=tavily_api_key,
                query=cleaned_query,
                max_results=safe_max_results,
                timeout_sec=timeout_sec,
            )
        except HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return json.dumps(
                {
                    "provider": "tavily",
                    "query": cleaned_query,
                    "count": 0,
                    "results": [],
                    "error": f"HTTP {exc.code}: {exc.reason}",
                    "detail": _truncate_text(body, 1500),
                },
                ensure_ascii=False,
            )
        except URLError as exc:
            return json.dumps(
                {
                    "provider": "tavily",
                    "query": cleaned_query,
                    "count": 0,
                    "results": [],
                    "error": f"请求失败: {exc.reason!s}",
                },
                ensure_ascii=False,
            )
        except TimeoutError:
            return json.dumps(
                {
                    "provider": "tavily",
                    "query": cleaned_query,
                    "count": 0,
                    "results": [],
                    "error": f"请求超时: {timeout_sec}s",
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            return json.dumps(
                {
                    "provider": "tavily",
                    "query": cleaned_query,
                    "count": 0,
                    "results": [],
                    "error": str(exc),
                },
                ensure_ascii=False,
            )

    return StructuredTool.from_function(
        name="web_search",
        description=(
            "搜索公开互联网信息，用于补充和核验近期事件、官方公告、治理论坛、"
            "安全公司报告、新闻、黑客事件、攻击归因、资金流公开进展等外部事实。"
            "当用户询问近期事实、公开事件背景、协议公告或需要来源校验的问题时，"
            "应优先调用本工具。返回 JSON，包含 title、url、content、score、published_date。"
            "注意：本工具提供公开资料线索；涉及链上数值时，应再结合数据库/RPC 工具交叉验证。"
        ),
        func=_invoke,
        args_schema=WebSearchInput,
    )
