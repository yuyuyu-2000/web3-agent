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
- public.justlend.day 是上游已计算的美国业务日期（YYYY-MM-DD）。用户询问“某天/当天/某日交易”且未明确指定其他时区时，必须使用 day 过滤；不得从 occurred 自行换算或反推 day。
- public.justlend.occurred 是同一事件对应的中国时间（UTC+8），默认仅用于展示具体时间和日内排序。只有用户明确要求中国时间/北京时间某日时才可按 occurred 过滤，并须披露口径。
- 禁止同时查询 day=用户日期 与 occurred LIKE '用户日期%' 后自行选择结果更多的日期口径。
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
