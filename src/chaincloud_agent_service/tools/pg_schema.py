"""PostgreSQL schema helper tools migrated from chain_bot."""

from __future__ import annotations

import json

import psycopg
from langchain_core.tools import StructuredTool
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier


def _split_table_name(table_name: str) -> tuple[str | None, str]:
    name = table_name.strip()
    if not name:
        raise ValueError("table_name 不能为空")
    if "." in name:
        schema, bare = name.split(".", 1)
        return schema.strip('" '), bare.strip('" ')
    return None, name.strip('" ')


def _list_tables(dsn: str) -> str:
    sql = """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY table_schema, table_name
    """
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        rows = conn.execute(sql).fetchall()
    tables = [
        f"{row['table_schema']}.{row['table_name']}"
        if row["table_schema"] != "public"
        else row["table_name"]
        for row in rows
    ]
    return json.dumps({"tables": tables, "count": len(tables)}, ensure_ascii=False)


def _get_table_schema(dsn: str, table_name: str) -> str:
    schema, bare_table = _split_table_name(table_name)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        if schema is None:
            found = conn.execute(
                """
                SELECT table_schema
                FROM information_schema.tables
                WHERE table_name = %s
                  AND table_schema NOT IN ('information_schema', 'pg_catalog')
                ORDER BY CASE WHEN table_schema = 'public' THEN 0 ELSE 1 END, table_schema
                LIMIT 1
                """,
                (bare_table,),
            ).fetchone()
            if found:
                schema = found["table_schema"]

        params: tuple[str, ...]
        if schema:
            cols_sql = """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = %s AND table_schema = %s
                ORDER BY ordinal_position
            """
            params = (bare_table, schema)
        else:
            cols_sql = """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = %s
                ORDER BY ordinal_position
            """
            params = (bare_table,)
        columns = conn.execute(cols_sql, params).fetchall()
        if not columns:
            return json.dumps(
                {"error": f"表 {table_name} 不存在或无权访问"},
                ensure_ascii=False,
            )

        if schema:
            sample_sql = SQL("SELECT * FROM {}.{} LIMIT 3").format(
                Identifier(schema),
                Identifier(bare_table),
            )
            qualified = f"{schema}.{bare_table}"
        else:
            sample_sql = SQL("SELECT * FROM {} LIMIT 3").format(Identifier(bare_table))
            qualified = bare_table
        sample_rows = conn.execute(sample_sql).fetchall()

    payload = {
        "table": qualified,
        "columns": [
            {
                "name": col["column_name"],
                "type": col["data_type"],
                "nullable": col["is_nullable"],
            }
            for col in columns
        ],
        "sample_rows": sample_rows,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def make_pg_list_tables_tool(dsn: str) -> StructuredTool:
    def _invoke() -> str:
        try:
            return _list_tables(dsn)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    return StructuredTool.from_function(
        name="postgres_list_tables",
        description=(
            "Schema recovery 工具：列出 PostgreSQL 中当前只读账号可见的业务表。"
            "仅在目标表名未知，或目标 SQL 已返回 undefined_table/表不存在错误时使用；"
            "不要在已知 table mapping 的正常查询前调用。"
        ),
        func=_invoke,
    )


def make_pg_table_schema_tool(dsn: str) -> StructuredTool:
    def _invoke(table_name: str) -> str:
        try:
            return _get_table_schema(dsn, table_name)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    return StructuredTool.from_function(
        name="postgres_table_schema",
        description=(
            "Schema recovery 工具：获取指定 PostgreSQL 表的字段、类型、可空信息和前 3 行样本。"
            "仅在目标 SQL 已返回 undefined_column、类型不匹配或其他 schema mismatch 时使用；"
            "不要重复确认 trusted schema 中已有的字段。"
            "table_name 支持 schema.table，例如 loan.aave_v3_eth_positions_borrow。"
        ),
        func=_invoke,
    )
