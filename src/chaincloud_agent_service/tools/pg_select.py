"""只读 PostgreSQL 查询工具（连接串应对应只读账号；此处允许 SELECT / WITH）。"""

from __future__ import annotations

import json

import psycopg
from langchain_core.tools import StructuredTool
from psycopg.rows import dict_row

from chaincloud_agent_service.tools.query_state import (
    clear_last_query_result,
    set_last_query_result,
)

_MAX_ROWS = 500


def _validate_pg_readonly(sql: str) -> str:
    s = sql.strip()
    if not s:
        raise ValueError("SQL 不能为空")
    core = s.rstrip().rstrip(";")
    if ";" in core:
        raise ValueError("仅允许单条语句")
    u = s.upper().lstrip()
    if u.startswith("SELECT") or u.startswith("WITH"):
        return s
    raise ValueError("仅允许 SELECT 或 WITH（CTE）查询")


def _run_select(dsn: str, sql: str) -> str:
    sql = _validate_pg_readonly(sql)
    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql)
            rows = cur.fetchmany(_MAX_ROWS)
    set_last_query_result(rows)
    return json.dumps(rows, ensure_ascii=False, default=str)


def make_pg_select_tool(dsn: str) -> StructuredTool:
    def _invoke(sql: str) -> str:
        try:
            return _run_select(dsn, sql)
        except Exception as e:
            clear_last_query_result()
            return f"查询失败: {e}"

    return StructuredTool.from_function(
        name="postgres_select",
        description=(
            "对只读 PostgreSQL 执行单条 SELECT 或 WITH（CTE）。返回至多 "
            f"{_MAX_ROWS} 行 JSON 数组（每行一个对象）。"
            "查业务表数据前，建议先用本工具对 information_schema / pg_catalog 做 SELECT 确认列与类型。"
            "请自行 LIMIT；禁止写操作。"
        ),
        func=_invoke,
    )
