"""调用本地 Node 脚本 decode-tx-input.js（AI-ContractParser），解析波场等交易 input。"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

_MAX_CAPTURE_CHARS = 500_000


def _default_cwd(script_path: Path) -> Path:
    """decode-tx-input.js 位于 …/scripts/decode/ 时，项目根为向上三级。"""
    resolved = script_path.resolve()
    return resolved.parent.parent.parent


def _normalize_tx_hash(tx: str) -> str:
    t = tx.strip().lower()
    if t.startswith("0x"):
        t = t[2:]
    if not re.fullmatch(r"[0-9a-f]{64}", t):
        raise ValueError("tx 须为 64 位十六进制交易哈希（可带或不带 0x 前缀）")
    return t


def _sanitize_address(address: str) -> str:
    a = address.strip()
    if not a or len(a) > 128:
        raise ValueError("address 无效或过长")
    if any(c in a for c in (";", "|", "&", "`", "$", "\n", "\r", "\x00")):
        raise ValueError("address 含非法字符")
    return a


def _run_node_decode(
    script_path: Path,
    cwd: Path,
    tx: str,
    address: str,
    timeout_sec: int,
) -> str:
    if not script_path.is_file():
        return f"脚本不存在: {script_path}"
    node = shutil.which("node")
    if not node:
        return "未找到 node 可执行文件，请安装 Node.js 并加入 PATH"

    cmd = [
        node,
        str(script_path.resolve()),
        "--tx",
        tx,
        "--address",
        address,
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd.resolve()),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"解码超时（>{timeout_sec}s）"
    except OSError as e:
        return f"执行失败: {e}"

    out = (proc.stdout or "") + (
        f"\n--- stderr ---\n{proc.stderr}"
        if proc.stderr and proc.stderr.strip()
        else ""
    )
    if len(out) > _MAX_CAPTURE_CHARS:
        out = out[:_MAX_CAPTURE_CHARS] + "\n... (输出已截断)"
    if proc.returncode != 0:
        return f"退出码 {proc.returncode}\n{out}".strip()
    return out.strip() or "(无 stdout 输出)"


class DecodeTxInputArgs(BaseModel):
    tx: str = Field(description="交易哈希：64 位十六进制，可选 0x 前缀")
    address: str = Field(
        description="合约或相关地址（如 Tron Base58 以 T 开头，或脚本支持的十六进制形式）"
    )


def make_contract_decode_tx_tool(
    script_path: str | None,
    project_root: str | None,
    timeout_sec: int = 120,
) -> StructuredTool:
    sp = Path(script_path).expanduser() if script_path else None
    cwd_override = Path(project_root).expanduser() if project_root else None

    def _invoke(tx: str, address: str) -> str:
        if not sp:
            return "未配置 CONTRACT_DECODE_SCRIPT_PATH"
        try:
            tx_n = _normalize_tx_hash(tx)
            addr = _sanitize_address(address)
        except ValueError as e:
            return str(e)
        cwd = cwd_override if cwd_override else _default_cwd(sp)
        if not cwd.is_dir():
            return f"工作目录不存在: {cwd}"
        return _run_node_decode(sp, cwd, tx_n, addr, timeout_sec)

    return StructuredTool.from_function(
        name="contract_decode_tx_input",
        description=(
            "使用本地 AI-ContractParser 的 decode-tx-input.js 解析指定链上交易的 input / 协议含义。"
            "需提供交易哈希 tx 与合约相关 address（与命令行 --tx、--address 一致）。"
            "依赖本机已安装 node，且脚本路径与工作目录（项目根）配置正确；输出为脚本 stdout。"
        ),
        func=_invoke,
        args_schema=DecodeTxInputArgs,
    )
