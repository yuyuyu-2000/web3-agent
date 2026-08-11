"""只读 ClickHouse 多数据源查询工具。

说明：
- 支持通过配置文件注册多个 ClickHouse 数据源。
- 每次工具调用都会创建一个新的 ClickHouse client。
- 这样可以避免大模型一次触发多个工具调用时，复用同一个 session 导致：
  "Attempt to execute concurrent queries within the same session"。
- 数据源语义说明由 config/clickhouse_datasources.json 维护，密码仍通过环境变量注入。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import clickhouse_connect
from langchain_core.tools import StructuredTool

_MAX_ROWS = 500
_DATASOURCE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class ClickHouseDatabaseHint:
    name: str
    description: str = ""


@dataclass(frozen=True)
class ClickHouseDataSource:
    id: str
    label: str
    host: str
    port: int
    username: str
    password: str
    database: str = "default"
    secure: bool = False
    description: str = ""
    databases: tuple[ClickHouseDatabaseHint, ...] = field(default_factory=tuple)
    usage_notes: tuple[str, ...] = field(default_factory=tuple)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "host": self.host,
            "port": self.port,
            "default_database": self.database,
            "secure": self.secure,
            "description": self.description,
            "databases": [
                {"name": item.name, "description": item.description}
                for item in self.databases
            ],
            "usage_notes": list(self.usage_notes),
        }


def _project_root() -> Path:
    # .../src/chaincloud_agent_service/tools/clickhouse_select.py -> parents[3] == 项目根
    return Path(__file__).resolve().parents[3]


def _normalize_clickhouse_endpoint(
    host: str,
    port: int,
    secure: bool,
) -> tuple[str, int, bool]:
    """
    支持 host 中误带 http(s):// 或与端口写在一起（如 10.0.0.1:8123），
    避免与单独传入的 port 叠加成「host:5886:8123」或「http://http://...」。
    """
    h = (host or "").strip()
    if not h:
        return h, port, secure

    if h.lower().startswith("https://"):
        secure = True
        h = h[8:]
    elif h.lower().startswith("http://"):
        h = h[7:]

    # IPv6: [addr]:port
    if h.startswith("["):
        bracket = h.find("]")
        if bracket != -1 and len(h) > bracket + 1 and h[bracket + 1] == ":":
            maybe = h[bracket + 2 :]
            if maybe.isdigit():
                return h[: bracket + 1], int(maybe), secure
        return h, port, secure

    if ":" in h:
        host_part, maybe_port = h.rsplit(":", 1)
        if maybe_port.isdigit():
            return host_part, int(maybe_port), secure

    return h, port, secure


def _validate_ch_readonly(sql: str) -> str:
    s = sql.strip()
    if not s:
        raise ValueError("SQL 不能为空")

    core = s.rstrip().rstrip(";")
    if ";" in core:
        raise ValueError("仅允许单条语句")

    u = s.upper().lstrip()
    if u.startswith(("SELECT", "WITH")):
        return s
    if u.startswith("SHOW ") or u.startswith("DESCRIBE ") or u.startswith("DESC "):
        return s

    raise ValueError("仅允许 SELECT、WITH 或 SHOW/DESCRIBE 等只读语句")


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _read_optional_json(path_str: str | None) -> dict[str, Any]:
    if not path_str:
        return {}
    raw = Path(path_str)
    path = raw if raw.is_absolute() else _project_root() / raw
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"ClickHouse 数据源配置文件 JSON 格式错误: {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"ClickHouse 数据源配置文件必须是 JSON object: {path}")
    return data


def _database_hints(items: Any) -> tuple[ClickHouseDatabaseHint, ...]:
    if not isinstance(items, list):
        return ()
    hints: list[ClickHouseDatabaseHint] = []
    for item in items:
        if isinstance(item, str):
            name = item.strip()
            if name:
                hints.append(ClickHouseDatabaseHint(name=name))
            continue
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if name:
                hints.append(
                    ClickHouseDatabaseHint(
                        name=name,
                        description=str(item.get("description", "")).strip(),
                    )
                )
    return tuple(hints)


def _usage_notes(items: Any) -> tuple[str, ...]:
    if not isinstance(items, list):
        return ()
    notes = [str(item).strip() for item in items if str(item).strip()]
    return tuple(notes)


def _datasource_from_mapping(item: Mapping[str, Any]) -> ClickHouseDataSource:
    datasource_id = str(item.get("id", "")).strip()
    if not datasource_id:
        raise ValueError("ClickHouse datasource 缺少 id")
    if not _DATASOURCE_ID_RE.match(datasource_id):
        raise ValueError(
            f"ClickHouse datasource id 仅允许字母、数字、下划线和中划线: {datasource_id}"
        )

    label = str(item.get("label", datasource_id)).strip() or datasource_id
    host = str(item.get("host", "")).strip()
    if not host:
        raise ValueError(f"ClickHouse datasource {datasource_id} 缺少 host")

    port = _as_int(item.get("port", 8123), 8123)
    secure = _as_bool(item.get("secure"), False)
    host, port, secure = _normalize_clickhouse_endpoint(host, port, secure)

    user_env = str(item.get("user_env", "")).strip()
    password_env = str(item.get("password_env", "")).strip()

    username = (
        os.environ.get(user_env, "").strip()
        if user_env
        else str(item.get("username", "")).strip()
    )
    if not username:
        username = "default"

    password = (
        os.environ.get(password_env, "")
        if password_env
        else str(item.get("password", ""))
    )

    database = (
        str(item.get("default_database", "")).strip()
        or str(item.get("database", "")).strip()
        or "default"
    )

    return ClickHouseDataSource(
        id=datasource_id,
        label=label,
        host=host,
        port=port,
        username=username,
        password=password,
        database=database,
        secure=secure,
        description=str(item.get("description", "")).strip(),
        databases=_database_hints(item.get("databases")),
        usage_notes=_usage_notes(item.get("usage_notes")),
    )


def _legacy_datasource_from_settings(settings: Any) -> ClickHouseDataSource | None:
    host = getattr(settings, "readonly_clickhouse_host", None)
    if not host:
        return None

    port = int(getattr(settings, "readonly_clickhouse_port", 8123))
    secure = bool(getattr(settings, "readonly_clickhouse_secure", False))
    host, port, secure = _normalize_clickhouse_endpoint(host, port, secure)
    database = getattr(settings, "readonly_clickhouse_database", "default") or "default"

    return ClickHouseDataSource(
        id=os.environ.get("READONLY_CLICKHOUSE_DATASOURCE_ID", "default").strip()
        or "default",
        label=os.environ.get(
            "READONLY_CLICKHOUSE_DATASOURCE_LABEL", "默认 ClickHouse 数据源"
        ).strip()
        or "默认 ClickHouse 数据源",
        host=host,
        port=port,
        username=getattr(settings, "readonly_clickhouse_user", "default") or "default",
        password=getattr(settings, "readonly_clickhouse_password", ""),
        database=database,
        secure=secure,
        description=(
            os.environ.get("READONLY_CLICKHOUSE_DATASOURCE_DESCRIPTION", "").strip()
            or "通过 READONLY_CLICKHOUSE_* 环境变量配置的默认 ClickHouse 只读数据源。"
        ),
        databases=(ClickHouseDatabaseHint(name=database),),
        usage_notes=("查询业务数据前先 SHOW TABLES / DESCRIBE TABLE。",),
    )


def load_clickhouse_datasources(settings: Any) -> list[ClickHouseDataSource]:
    """Load ClickHouse datasource definitions.

    Preferred path:
    - config/clickhouse_datasources.json stores host/port/database/semantic descriptions.
    - credentials are loaded through per-datasource env vars.

    Backward compatibility:
    - if READONLY_CLICKHOUSE_HOST is configured, it is also exposed as a legacy datasource.
    """

    datasources: dict[str, ClickHouseDataSource] = {}

    config_path = getattr(settings, "clickhouse_datasources_path", None)
    data = _read_optional_json(config_path)
    raw_items = data.get("datasources", [])
    if raw_items and not isinstance(raw_items, list):
        raise ValueError("clickhouse_datasources.json 中 datasources 必须是 list")

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        datasource = _datasource_from_mapping(item)
        datasources[datasource.id] = datasource

    # Prefer the explicit multi-datasource registry when it is present.
    # The local .env may still contain legacy READONLY_CLICKHOUSE_* values;
    # adding them automatically would create an extra "default" datasource and
    # make the agent see duplicate/ambiguous ClickHouse entries.
    # Keep legacy READONLY_CLICKHOUSE_* as backward compatibility only when no
    # JSON datasource registry is configured.
    if not datasources:
        legacy = _legacy_datasource_from_settings(settings)
        if legacy is not None:
            datasources[legacy.id] = legacy

    return list(datasources.values())


def _datasource_map(
    datasources: Sequence[ClickHouseDataSource] | Mapping[str, ClickHouseDataSource],
) -> dict[str, ClickHouseDataSource]:
    if isinstance(datasources, Mapping):
        return dict(datasources)
    return {item.id: item for item in datasources}


def make_clickhouse_list_datasources_tool(
    datasources: Sequence[ClickHouseDataSource] | Mapping[str, ClickHouseDataSource],
) -> StructuredTool:
    datasource_by_id = _datasource_map(datasources)

    def _invoke() -> str:
        payload = [item.to_public_dict() for item in datasource_by_id.values()]
        return json.dumps(payload, ensure_ascii=False, default=str)

    return StructuredTool.from_function(
        name="clickhouse_list_datasources",
        description=(
            "列出当前 Agent 可用的 ClickHouse 数据源、默认库和语义说明。"
            "当用户没有明确指定数据源，或问题可能需要查询 ClickHouse 时，应先调用本工具，"
            "再根据 description、databases、usage_notes 选择合适的 datasource_id。"
            "本工具不会返回密码。"
        ),
        func=_invoke,
    )


def make_clickhouse_select_tool(
    *,
    datasources: Sequence[ClickHouseDataSource]
    | Mapping[str, ClickHouseDataSource]
    | None = None,
    host: str | None = None,
    port: int = 8123,
    username: str = "default",
    password: str = "",
    database: str = "default",
    secure: bool = False,
) -> StructuredTool:
    """Build a read-only ClickHouse SELECT tool.

    New usage:
        make_clickhouse_select_tool(datasources=[...])

    Backward-compatible usage:
        make_clickhouse_select_tool(host=..., port=..., username=..., ...)
    """

    if datasources is None:
        if not host:
            raise ValueError("ClickHouse tool 需要 datasources 或 host")
        normalized_host, normalized_port, normalized_secure = (
            _normalize_clickhouse_endpoint(host, port, secure)
        )
        datasources = [
            ClickHouseDataSource(
                id="default",
                label="默认 ClickHouse 数据源",
                host=normalized_host,
                port=normalized_port,
                username=username,
                password=password,
                database=database,
                secure=normalized_secure,
                description="通过 legacy 参数创建的 ClickHouse 数据源。",
                databases=(ClickHouseDatabaseHint(name=database),),
            )
        ]

    datasource_by_id = _datasource_map(datasources)
    if not datasource_by_id:
        raise ValueError("ClickHouse tool 至少需要一个 datasource")

    default_datasource_id = next(iter(datasource_by_id))
    datasource_summary = ", ".join(
        f"{item.id}({item.host}:{item.port}/{item.database})"
        for item in datasource_by_id.values()
    )

    def _invoke(sql: str, datasource_id: str | None = None) -> str:
        client = None
        selected_id = (datasource_id or default_datasource_id).strip()
        datasource = datasource_by_id.get(selected_id)
        if datasource is None:
            return json.dumps(
                {
                    "error": f"未知 ClickHouse datasource_id: {selected_id}",
                    "available_datasources": list(datasource_by_id),
                },
                ensure_ascii=False,
            )

        try:
            sql = _validate_ch_readonly(sql)

            # 每次调用创建独立 client，避免多个 tool call 共享同一个 ClickHouse session。
            client = clickhouse_connect.get_client(
                host=datasource.host,
                port=datasource.port,
                username=datasource.username,
                password=datasource.password,
                database=datasource.database,
                secure=datasource.secure,
            )

            result = client.query(sql)
            rows = [
                dict(zip(result.column_names, row))
                for row in result.result_rows[:_MAX_ROWS]
            ]
            return json.dumps(
                {
                    "datasource_id": datasource.id,
                    "datasource_label": datasource.label,
                    "default_database": datasource.database,
                    "row_count": len(rows),
                    "rows": rows,
                },
                ensure_ascii=False,
                default=str,
            )
        except Exception as e:
            return json.dumps(
                {
                    "datasource_id": datasource.id,
                    "datasource_label": datasource.label,
                    "error": f"查询失败: {e}",
                },
                ensure_ascii=False,
                default=str,
            )
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    # close 失败不影响工具查询结果，避免把关闭连接问题暴露给用户。
                    pass

    return StructuredTool.from_function(
        name="clickhouse_select",
        description=(
            "对一个已配置的 ClickHouse 数据源执行单条只读语句。参数："
            "`datasource_id` 为 clickhouse_list_datasources 返回的数据源 id，"
            "`sql` 为 SELECT、WITH、SHOW 或 DESCRIBE/DESC 语句。"
            f"当前可用数据源：{datasource_summary}。"
            "当用户没有明确数据源时，应先调用 clickhouse_list_datasources，"
            "根据数据源 description / databases / usage_notes 选择 datasource_id。"
            "查业务数据前建议先 SHOW DATABASES、SHOW TABLES、DESCRIBE TABLE 或 SHOW CREATE TABLE，"
            f"返回至多 {_MAX_ROWS} 行 JSON。查询业务数据必须使用 LIMIT；禁止写操作。"
        ),
        func=_invoke,
    )
