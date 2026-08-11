"""以太坊执行客户端 JSON-RPC（HTTP POST，单端点）。"""

from __future__ import annotations

import itertools
import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_MAX_RESPONSE_CHARS = 400_000
_RPC_IDS = itertools.count(1)

# 禁止经本工具提交交易或明显改链配置（节点若暴露这些接口，仍不应由 Agent 调用）
_FORBIDDEN_METHODS = frozenset(
    {
        "eth_sendTransaction",
        "eth_sendRawTransaction",
        "eth_signTransaction",
    }
)


def _parse_params(params: Any) -> list[Any]:
    """JSON-RPC 的 params 在以太坊规范中为数组。"""
    if params is None:
        return []
    if isinstance(params, list):
        return params
    if isinstance(params, str):
        s = params.strip()
        if not s:
            return []
        parsed = json.loads(s)
        if not isinstance(parsed, list):
            raise ValueError('params 必须是 JSON 数组，例如 [] 或 ["0x...", "latest"]')
        return parsed
    raise ValueError("params 须为数组或 JSON 数组字符串")


def _post_json(url: str, body: dict[str, Any]) -> str:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(
        url.rstrip("/"),
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if len(raw) > _MAX_RESPONSE_CHARS:
        raw = raw[:_MAX_RESPONSE_CHARS] + "\n... (响应已截断)"
    return raw


class EthereumJsonRpcInput(BaseModel):
    method: str = Field(
        description=(
            "JSON-RPC 方法名，如 eth_blockNumber、eth_getBlockByNumber、"
            "eth_getTransactionByHash、eth_call、eth_getLogs"
        )
    )
    params: str | list[Any] | None = Field(
        default=None,
        description=(
            "对应方法的 params，必须是 JSON 数组；可省略表示 []。"
            '例：eth_blockNumber 省略；eth_getBlockByNumber 用 ["0x1234", false]；'
            'eth_getTransactionByHash 用 ["0x..."]。'
        ),
    )


def make_ethereum_jsonrpc_tool(rpc_url: str | None) -> StructuredTool:
    def _invoke(method: str, params: str | list[Any] | None = None) -> str:
        if not rpc_url or not rpc_url.strip():
            return "未配置 ETHEREUM_JSONRPC_URL"
        m = method.strip()
        if not m:
            return "method 不能为空"
        if m in _FORBIDDEN_METHODS:
            return f"本工具禁止调用方法: {m}"
        try:
            plist = _parse_params(params)
            req_body: dict[str, Any] = {
                "jsonrpc": "2.0",
                "method": m,
                "params": plist,
                "id": next(_RPC_IDS),
            }
            return _post_json(rpc_url.strip(), req_body)
        except ValueError as e:
            return str(e)
        except json.JSONDecodeError as e:
            return f"params 不是合法 JSON: {e}"
        except HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            return f"HTTP {e.code}: {e.reason}\n{err_body}".strip()
        except URLError as e:
            return f"请求失败: {e.reason!s}"

    return StructuredTool.from_function(
        name="ethereum_jsonrpc",
        description=(
            "对已配置的以太坊（或兼容链）节点发起单次 JSON-RPC 2.0 调用："
            "HTTP POST 到 ETHEREUM_JSONRPC_URL，请求体含 jsonrpc、method、params、id。"
            "只读查询示例：eth_blockNumber（params 省略或 []）；"
            'eth_getTransactionByHash（["0x..."]）；'
            'eth_getBlockByNumber（["latest", false]）。'
            "响应为节点返回的 JSON 字符串（含 result 或 error）；"
            "勿调用 eth_sendTransaction / eth_sendRawTransaction。"
        ),
        func=_invoke,
        args_schema=EthereumJsonRpcInput,
    )
