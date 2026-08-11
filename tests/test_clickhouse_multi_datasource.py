from __future__ import annotations

import json
from pathlib import Path

from chaincloud_agent_service.config import load_settings
from chaincloud_agent_service.tools.clickhouse_select import (
    load_clickhouse_datasources,
    make_clickhouse_list_datasources_tool,
    make_clickhouse_select_tool,
)
from chaincloud_agent_service.tools.registry import get_tools


def _write_datasource_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "datasources": [
                    {
                        "id": "trx_5886",
                        "label": "TRON 5886",
                        "host": "10.8.6.153",
                        "port": 5886,
                        "default_database": "trx",
                        "user_env": "CLICKHOUSE_TRX_5886_USER",
                        "password_env": "CLICKHOUSE_TRX_5886_PASSWORD",
                        "description": "TRON 链数据源",
                        "databases": [{"name": "trx", "description": "TRON 数据库"}],
                        "usage_notes": ["用于 TRON 查询"],
                    },
                    {
                        "id": "analytics_5887",
                        "label": "Analytics 5887",
                        "host": "10.8.6.153",
                        "port": 5887,
                        "default_database": "default",
                        "user_env": "CLICKHOUSE_ANALYTICS_5887_USER",
                        "password_env": "CLICKHOUSE_ANALYTICS_5887_PASSWORD",
                        "description": "待补充语义说明的数据源",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_load_clickhouse_multi_datasources(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "clickhouse_datasources.json"
    _write_datasource_config(config_path)

    monkeypatch.setenv("CLICKHOUSE_DATASOURCES_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CLICKHOUSE_TRX_5886_USER", "trx_user")
    monkeypatch.setenv("CLICKHOUSE_TRX_5886_PASSWORD", "trx_secret")
    monkeypatch.setenv("CLICKHOUSE_ANALYTICS_5887_USER", "analytics_user")
    monkeypatch.setenv("CLICKHOUSE_ANALYTICS_5887_PASSWORD", "analytics_secret")
    monkeypatch.setenv("READONLY_CLICKHOUSE_HOST", "legacy-host")
    monkeypatch.setenv("READONLY_CLICKHOUSE_PORT", "5886")

    settings = load_settings()
    datasources = load_clickhouse_datasources(settings)
    by_id = {item.id: item for item in datasources}

    assert set(by_id) == {"trx_5886", "analytics_5887"}
    assert "default" not in by_id
    assert by_id["trx_5886"].username == "trx_user"
    assert by_id["trx_5886"].password == "trx_secret"
    assert by_id["analytics_5887"].port == 5887
    assert by_id["analytics_5887"].username == "analytics_user"


def test_clickhouse_tools_expose_datasource_metadata(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "clickhouse_datasources.json"
    _write_datasource_config(config_path)

    monkeypatch.setenv("CLICKHOUSE_DATASOURCES_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CLICKHOUSE_TRX_5886_USER", "trx_user")
    monkeypatch.setenv("CLICKHOUSE_ANALYTICS_5887_USER", "analytics_user")

    settings = load_settings()
    datasources = load_clickhouse_datasources(settings)

    list_tool = make_clickhouse_list_datasources_tool(datasources)
    select_tool = make_clickhouse_select_tool(datasources=datasources)

    assert list_tool.name == "clickhouse_list_datasources"
    assert select_tool.name == "clickhouse_select"
    assert "datasource_id" in select_tool.description
    assert "analytics_5887" in select_tool.description
    assert "SHOW DATABASES" in select_tool.description


def test_registry_registers_multi_datasource_tools(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "clickhouse_datasources.json"
    _write_datasource_config(config_path)

    monkeypatch.setenv("CLICKHOUSE_DATASOURCES_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CLICKHOUSE_TRX_5886_USER", "trx_user")
    monkeypatch.setenv("CLICKHOUSE_ANALYTICS_5887_USER", "analytics_user")

    settings = load_settings()
    tools = get_tools(settings)
    names = {tool.name for tool in tools}

    assert "clickhouse_list_datasources" in names
    assert "clickhouse_select" in names
