from __future__ import annotations

from chaincloud_agent_service.tools.pg_schema import (
    make_pg_list_tables_tool,
    make_pg_table_schema_tool,
)


def test_discovery_tool_descriptions_are_recovery_only() -> None:
    list_tool = make_pg_list_tables_tool("postgresql://unused")
    schema_tool = make_pg_table_schema_tool("postgresql://unused")

    assert "Schema recovery" in list_tool.description
    assert "undefined_table" in list_tool.description
    assert "正常查询前" in list_tool.description
    assert "查询表数据前可先调用" not in list_tool.description

    assert "Schema recovery" in schema_tool.description
    assert "undefined_column" in schema_tool.description
    assert "schema mismatch" in schema_tool.description
    assert "不要重复确认" in schema_tool.description
