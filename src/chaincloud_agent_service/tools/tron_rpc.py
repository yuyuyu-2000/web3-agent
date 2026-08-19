"""波场全节点 / Solidity 节点 HTTP API 调用（只读交互，POST + JSON）。"""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

_MAX_RESPONSE_CHARS = 400_000
_PUBLIC_TRON_RPC = "https://api.trongrid.io"
_TXID_RE = re.compile(r"^(?:0x)?([0-9a-fA-F]{64})$")


def _safe_path(path: str) -> str:
    p = path.strip()
    if not p.startswith("/"):
        p = "/" + p
    if ".." in p or p.startswith("//"):
        raise ValueError("非法 path")
    return p


def _post(base: str, path: str, body: object) -> str:
    base = base.rstrip("/")
    url = base + path
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if len(raw) > _MAX_RESPONSE_CHARS:
        raw = raw[:_MAX_RESPONSE_CHARS] + "\n... (响应已截断)"
    return raw


def _parse_body(body_json: Any) -> object:
    """兼容模型把 body 传成 dict / list、字符串或省略（function calling 常传对象而非字符串）。"""
    if body_json is None:
        return {}
    if isinstance(body_json, dict):
        return body_json
    if isinstance(body_json, list):
        return body_json
    if not isinstance(body_json, str):
        return {}
    s = body_json.strip()
    if not s:
        return {}
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"body_json 不是合法 JSON: {e}") from e
    if not isinstance(parsed, (dict, list)):
        raise ValueError("body_json 解析后须为 JSON 对象或数组")
    return parsed


class TronNodeInput(BaseModel):
    target: Literal["full", "solidity"] = Field(
        description="full 使用 TRON_FULL_RPC；solidity 使用 TRON_SOLIDITY_RPC（固化数据）"
    )
    path: str = Field(description="节点 HTTP 路径，以 / 开头，例如 /wallet/getnowblock")
    body_json: str | dict | list | None = Field(
        default=None,
        description=(
            "POST 请求体：可省略（等价 {}）。"
            "可直接传 JSON 对象 {}，或 JSON 字符串如 '{}'；查最新块时传空对象即可。"
        ),
    )


class TronTransactionInput(BaseModel):
    txid: str = Field(
        description="TRON 交易哈希（64 个十六进制字符，可带 0x 前缀）"
    )


def _normalize_txid(txid: str) -> str:
    match = _TXID_RE.fullmatch(txid.strip())
    if not match:
        raise ValueError("txid 必须是 64 个十六进制字符（可带 0x 前缀）")
    return match.group(1).lower()


def make_tron_transaction_lookup_tool(
    rpc_url: str = _PUBLIC_TRON_RPC,
) -> StructuredTool:
    """Create a read-only transaction lookup backed by TRON HTTP APIs."""

    def _invoke(txid: str) -> str:
        try:
            normalized_txid = _normalize_txid(txid)
        except ValueError as exc:
            return json.dumps(
                {"txid": txid, "error": str(exc)}, ensure_ascii=False
            )

        body = {"value": normalized_txid}
        endpoints = {
            "transaction": "/wallet/gettransactionbyid",
            "transaction_info": "/wallet/gettransactioninfobyid",
        }
        combined: dict[str, Any] = {
            "provider": "tron_public_node",
            "txid": normalized_txid,
            "transaction": None,
            "transaction_info": None,
            "errors": {},
        }

        for result_key, path in endpoints.items():
            try:
                raw = _post(rpc_url, path, body)
                combined[result_key] = json.loads(raw)
            except json.JSONDecodeError:
                combined["errors"][result_key] = "节点返回的内容不是合法 JSON"
            except HTTPError as exc:
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    detail = ""
                combined["errors"][result_key] = {
                    "error": f"HTTP {exc.code}: {exc.reason}",
                    "detail": detail[:2000],
                }
            except URLError as exc:
                combined["errors"][result_key] = f"请求失败: {exc.reason!s}"
            except TimeoutError:
                combined["errors"][result_key] = "请求超时: 60s"
            except Exception as exc:
                combined["errors"][result_key] = str(exc)

        if not combined["errors"]:
            del combined["errors"]
        return json.dumps(combined, ensure_ascii=False, default=str)

    return StructuredTool.from_function(
        name="get_tron_transaction",
        description=(
            "通过 TRON 公共节点按交易哈希查询链上数据。工具只执行两个固定的只读接口："
            "/wallet/gettransactionbyid 获取交易本体，"
            "/wallet/gettransactioninfobyid 获取执行回执、费用、日志和内部交易；"
            "随后合并结果供分析。该工具不能签名、广播交易或执行任何写链操作。"
        ),
        func=_invoke,
        args_schema=TronTransactionInput,
    )


def make_tron_node_tool(
    full_rpc: str | None, solidity_rpc: str | None
) -> StructuredTool:
    def _invoke(
        target: Literal["full", "solidity"],
        path: str,
        body_json: str | dict | list | None = None,
    ) -> str:
        base = full_rpc if target == "full" else solidity_rpc
        if not base or not base.strip():
            return (
                f"未配置 {'TRON_FULL_RPC' if target == 'full' else 'TRON_SOLIDITY_RPC'}"
            )
        try:
            path_ok = _safe_path(path)
            body = _parse_body(body_json)
            return _post(base.strip(), path_ok, body)
        except ValueError as e:
            return str(e)
        except HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            return f"HTTP {e.code}: {e.reason}\n{err_body}".strip()
        except URLError as e:
            return f"请求失败: {e.reason!s}"

    return StructuredTool.from_function(
        name="tron_node_request",
        description=(
            "调用已配置的波场节点 HTTP API（与 tron-java full 节点 / Solidity 节点兼容）。"
            "使用 POST + application/json。"
            "full 节点常见 path：/wallet/getnowblock（body_json 传 {}）；"
            "solidity 节点常见 path：/walletsolidity/getnowblock（body_json 传 {}）。"
            "其他常见 path：/wallet/getaccount（含 address、visible:true）、"
            "/wallet/triggerconstantcontract 等。"
            "具体字段以节点接口返回为准；勿猜测未文档化的接口。"
        ),
        func=_invoke,
        args_schema=TronNodeInput,
    )
