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
    parts: list[str] = [
        """已知数据源与查询约定：
- JustLend 协议对应 PostgreSQL 表 public.justlend；处理 JustLend 查询时直接使用该表，不要先调用 postgres_list_tables。
- public.justlend 的主要金额字段为 amount_usd，业务日期字段为 day，事件时间字段为 occurred；回答中必须说明采用的日期字段和时区口径。
- 用户未定义“大额”时，默认采用 amount_usd >= 100000 USD；如使用当日金额前 5% 等动态口径，必须明确披露计算方式。
- 优先用一条聚合 SQL 同时确认日期覆盖、记录数和金额分布，再查询明细；不要为每个统计量单独探查。
- 数据库查询成功返回空数组时，只要 SQL 条件可追溯，应如实表述为该口径下未发现匹配记录，不得误报为查询未执行。"""
    ]
    s = load_agent_schema_markdown(settings)
    if s.strip():
        parts.append(s.strip())
    r = load_agent_response_style_markdown(settings)
    if r.strip():
        parts.append(r.strip())
    c = load_agent_contract_decode_markdown(settings)
    if c.strip():
        parts.append(c.strip())
    return "\n\n---\n\n".join(parts)
