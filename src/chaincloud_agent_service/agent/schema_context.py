"""从仓库中的 Markdown 加载供模型使用的系统提示片段（schema、回答风格等；不写入 checkpoint）。"""

from __future__ import annotations

from pathlib import Path

from chaincloud_agent_service.config import Settings


def _project_root() -> Path:
    # .../src/chaincloud_agent_service/agent/schema_context.py -> parents[3] == 项目根
    return Path(__file__).resolve().parents[3]


def _load_optional_markdown(path_str: str | None) -> str:
    if not path_str:
        return ""
    raw = Path(path_str)
    path = raw if raw.is_absolute() else _project_root() / raw
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def load_agent_schema_markdown(settings: Settings) -> str:
    """库表说明 Markdown；路径不存在或文件缺失则返回空字符串。"""
    return _load_optional_markdown(settings.agent_database_schema_path)


def load_agent_response_style_markdown(settings: Settings) -> str:
    """全局回答风格 Markdown；路径不存在或文件缺失则返回空字符串。"""
    return _load_optional_markdown(settings.agent_response_style_path)


def load_agent_contract_decode_markdown(settings: Settings) -> str:
    """合约解码流程与触发条件；路径不存在或文件缺失则返回空字符串。"""
    return _load_optional_markdown(settings.agent_contract_decode_path)


def build_agent_system_prompt(settings: Settings) -> str:
    """合并 schema、回答风格、合约解码说明为一条系统提示（中间用分隔线）。"""
    parts: list[str] = []
    s = load_agent_schema_markdown(settings)
    if s.strip():
        parts.append(s.strip())
    r = load_agent_response_style_markdown(settings)
    if r.strip():
        parts.append(r.strip())
    c = load_agent_contract_decode_markdown(settings)
    if c.strip():
        parts.append(c.strip())
    if not parts:
        return ""
    return "\n\n---\n\n".join(parts)
