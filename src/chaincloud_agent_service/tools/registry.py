"""
工具注册表：将 PG、ClickHouse 等封装为 LangChain 工具列表，供 graph 绑定。
"""

from __future__ import annotations

import os
from typing import Any

from chaincloud_agent_service.config import Settings
from chaincloud_agent_service.tools.chart_tools import make_chart_tools
from chaincloud_agent_service.tools.clickhouse_select import (
    load_clickhouse_datasources,
    make_clickhouse_list_datasources_tool,
    make_clickhouse_select_tool,
)
from chaincloud_agent_service.tools.pg_select import make_pg_select_tool
from chaincloud_agent_service.tools.pg_schema import (
    make_pg_list_tables_tool,
    make_pg_table_schema_tool,
)
from chaincloud_agent_service.tools.contract_decode_tx import (
    make_contract_decode_tx_tool,
)
from chaincloud_agent_service.tools.dashboard import make_dashboard_tool
from chaincloud_agent_service.tools.eth_jsonrpc import make_ethereum_jsonrpc_tool
from chaincloud_agent_service.tools.kb_search import make_kb_search_tool
from chaincloud_agent_service.tools.scheduler_tools import make_scheduler_tool
from chaincloud_agent_service.tools.tron_rpc import (
    make_tron_node_tool,
    make_tron_transaction_lookup_tool,
)
from chaincloud_agent_service.tools.web_search import make_web_search_tool
from chaincloud_agent_service.tools.monitor_tools import make_monitor_tools


def get_tools(settings: Settings) -> list[Any]:
    """返回绑定到模型的工具列表（StructuredTool 等）。"""
    tools: list[Any] = []
    if settings.readonly_database_url:
        tools.append(make_pg_select_tool(settings.readonly_database_url))
        tools.append(make_pg_list_tables_tool(settings.readonly_database_url))
        tools.append(make_pg_table_schema_tool(settings.readonly_database_url))
        if os.environ.get("KB_ENABLED", "").strip().lower() in {"1", "true", "yes"}:
            tools.append(make_kb_search_tool(settings.readonly_database_url))
    if settings.web_search_enabled:
        tools.append(
            make_web_search_tool(
                provider=settings.web_search_provider,
                tavily_api_key=settings.tavily_api_key,
                default_max_results=settings.web_search_max_results,
                timeout_sec=settings.web_search_timeout_sec,
            )
        )
    tools.extend(make_chart_tools(settings.readonly_database_url))
    tools.append(make_dashboard_tool())
    tools.append(make_scheduler_tool())
    tools.append(make_tron_transaction_lookup_tool())
    if settings.monitor_enabled and settings.monitor_database_url:
        tools.extend(make_monitor_tools())
    clickhouse_datasources = load_clickhouse_datasources(settings)
    if clickhouse_datasources:
        tools.append(make_clickhouse_list_datasources_tool(clickhouse_datasources))
        tools.append(make_clickhouse_select_tool(datasources=clickhouse_datasources))
    if settings.tron_full_rpc or settings.tron_solidity_rpc:
        tools.append(
            make_tron_node_tool(settings.tron_full_rpc, settings.tron_solidity_rpc)
        )
    if settings.ethereum_jsonrpc_url:
        tools.append(make_ethereum_jsonrpc_tool(settings.ethereum_jsonrpc_url))
    if settings.contract_decode_script_path:
        tools.append(
            make_contract_decode_tx_tool(
                settings.contract_decode_script_path,
                settings.contract_parser_cwd,
                timeout_sec=settings.contract_decode_timeout_sec,
            )
        )
    return tools
