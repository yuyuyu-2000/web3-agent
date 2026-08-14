"""从环境变量读取配置（首版不使用 pydantic-settings）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _load_dotenv_from_project() -> None:
    """从仓库/项目根目录的 `.env` 加载（沿当前包路径向上查找第一个 `.env`）。已存在的环境变量不被覆盖。"""
    here = Path(__file__).resolve()
    for directory in (here.parent, *here.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return
    load_dotenv(override=False)


_load_dotenv_from_project()


@dataclass(frozen=True)
class Settings:
    database_url: str
    readonly_database_url: str | None
    readonly_clickhouse_host: str | None
    readonly_clickhouse_port: int
    readonly_clickhouse_user: str
    readonly_clickhouse_password: str
    readonly_clickhouse_database: str
    readonly_clickhouse_secure: bool
    clickhouse_datasources_path: str | None
    agent_database_schema_path: str | None
    agent_response_style_path: str | None
    agent_contract_decode_path: str | None
    openai_api_key: str
    openai_base_url: str | None
    openai_model: str
    openai_timeout_sec: int
    openai_max_retries: int
    model_context_window: int
    max_input_tokens: int
    reserved_output_tokens: int
    tool_result_store_path: str
    tool_result_compression_threshold_bytes: int
    tool_result_preview_chars: int
    rolling_summary_trigger_ratio: float
    rolling_summary_recent_messages: int
    rolling_summary_reactive_recent_messages: int
    rolling_summary_max_input_tokens: int
    rolling_summary_max_failures: int
    chat_api_token: str | None
    web_search_enabled: bool
    web_search_provider: str
    tavily_api_key: str | None
    web_search_max_results: int
    web_search_timeout_sec: int
    tron_full_rpc: str | None
    tron_solidity_rpc: str | None
    ethereum_jsonrpc_url: str | None
    contract_decode_script_path: str | None
    contract_parser_cwd: str | None
    contract_decode_timeout_sec: int
    memory_store_backend: str
    memory_database_url: str | None
    memory_postgres_table: str
    memory_postgres_auto_create: bool
    auth_database_url: str | None
    auth_users_table: str
    auth_postgres_auto_create: bool
    auth_token_secret: str
    auth_token_expire_minutes: int
    max_tool_retries: int = 2
    max_step_retries: int = 2
    max_total_tool_calls: int = 16
    max_step_tool_calls: int = 6
    max_direct_tool_calls: int = 6
    monitor_enabled: bool = False
    monitor_database_url: str | None = None
    monitor_table_prefix: str = "monitor"
    monitor_scan_interval_sec: int = 30
    monitor_transaction_database_url: str | None = None
    monitor_transaction_table: str = "transactions"
    monitor_transaction_columns: str = ""
    monitor_scan_batch_size: int = 1000
    monitor_process_existing: bool = False
    planned_reviewer_low_model: str | None = None
    planned_reviewer_high_model: str | None = None


def load_settings() -> Settings:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    ro = os.environ.get("READONLY_DATABASE_URL", "").strip()
    ch_host = os.environ.get("READONLY_CLICKHOUSE_HOST", "").strip() or None
    ch_port = int(os.environ.get("READONLY_CLICKHOUSE_PORT", "8123").strip() or "8123")
    ch_user = os.environ.get("READONLY_CLICKHOUSE_USER", "default").strip() or "default"
    ch_password = os.environ.get("READONLY_CLICKHOUSE_PASSWORD", "")
    ch_database = (
        os.environ.get("READONLY_CLICKHOUSE_DATABASE", "default").strip() or "default"
    )
    ch_secure = os.environ.get("READONLY_CLICKHOUSE_SECURE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    clickhouse_datasources_path_raw = os.environ.get(
        "CLICKHOUSE_DATASOURCES_CONFIG_PATH"
    )
    if clickhouse_datasources_path_raw is None:
        clickhouse_datasources_path = "config/clickhouse_datasources.json"
    else:
        clickhouse_datasources_path = clickhouse_datasources_path_raw.strip() or None
    schema_path_raw = os.environ.get("AGENT_DATABASE_SCHEMA_PATH")
    if schema_path_raw is None:
        agent_database_schema_path = "config/agent_database_schema.md"
    else:
        agent_database_schema_path = schema_path_raw.strip() or None
    style_path_raw = os.environ.get("AGENT_RESPONSE_STYLE_PATH")
    if style_path_raw is None:
        agent_response_style_path = "config/agent_response_style.md"
    else:
        agent_response_style_path = style_path_raw.strip() or None
    decode_md_raw = os.environ.get("AGENT_CONTRACT_DECODE_PATH")
    if decode_md_raw is None:
        agent_contract_decode_path = "config/agent_contract_decode.md"
    else:
        agent_contract_decode_path = decode_md_raw.strip() or None
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base = os.environ.get("OPENAI_BASE_URL", "").strip()
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    default_low_reviewer_model = (
        "deepseek-chat" if model.lower() == "deepseek-reasoner" else model
    )
    planned_reviewer_low_model = (
        os.environ.get("PLANNED_REVIEWER_LOW_MODEL", default_low_reviewer_model).strip()
        or default_low_reviewer_model
    )
    planned_reviewer_high_model = (
        os.environ.get("PLANNED_REVIEWER_HIGH_MODEL", model).strip() or model
    )
    try:
        openai_timeout_sec = int(
            os.environ.get("OPENAI_TIMEOUT_SEC", "60").strip() or "60"
        )
    except ValueError:
        openai_timeout_sec = 60
    if openai_timeout_sec < 5:
        openai_timeout_sec = 5
    if openai_timeout_sec > 300:
        openai_timeout_sec = 300
    try:
        openai_max_retries = int(
            os.environ.get("OPENAI_MAX_RETRIES", "1").strip() or "1"
        )
    except ValueError:
        openai_max_retries = 1
    if openai_max_retries < 0:
        openai_max_retries = 0
    if openai_max_retries > 5:
        openai_max_retries = 5

    def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(os.environ.get(name, str(default)).strip() or str(default))
        except ValueError:
            value = default
        return min(max(value, minimum), maximum)

    max_tool_retries = bounded_int("MAX_TOOL_RETRIES", 2, 0, 10)
    max_step_retries = bounded_int("MAX_STEP_RETRIES", 2, 0, 10)
    max_total_tool_calls = bounded_int("MAX_TOTAL_TOOL_CALLS", 16, 1, 100)
    max_step_tool_calls = bounded_int("MAX_STEP_TOOL_CALLS", 6, 1, 50)
    max_direct_tool_calls = bounded_int("MAX_DIRECT_TOOL_CALLS", 6, 1, 50)
    model_context_window = bounded_int("MODEL_CONTEXT_WINDOW", 128000, 1024, 2000000)
    reserved_output_tokens = bounded_int("RESERVED_OUTPUT_TOKENS", 8000, 128, model_context_window - 1)
    max_input_tokens = bounded_int(
        "MAX_INPUT_TOKENS", min(96000, model_context_window - reserved_output_tokens),
        256, model_context_window - reserved_output_tokens,
    )
    tool_result_store_path = (
        os.environ.get("TOOL_RESULT_STORE_PATH", "tool_results").strip()
        or "tool_results"
    )
    tool_result_compression_threshold_bytes = bounded_int(
        "TOOL_RESULT_COMPRESSION_THRESHOLD_BYTES", 16000, 256, 100000000
    )
    tool_result_preview_chars = bounded_int(
        "TOOL_RESULT_PREVIEW_CHARS", 1000, 0, 10000
    )
    try:
        rolling_summary_trigger_ratio = float(
            os.environ.get("ROLLING_SUMMARY_TRIGGER_RATIO", "0.70")
        )
    except ValueError:
        rolling_summary_trigger_ratio = 0.70
    rolling_summary_trigger_ratio = min(max(rolling_summary_trigger_ratio, 0.1), 0.95)
    rolling_summary_recent_messages = bounded_int(
        "ROLLING_SUMMARY_RECENT_MESSAGES", 12, 2, 200
    )
    rolling_summary_reactive_recent_messages = bounded_int(
        "ROLLING_SUMMARY_REACTIVE_RECENT_MESSAGES", 4, 1, 100
    )
    rolling_summary_max_input_tokens = bounded_int(
        "ROLLING_SUMMARY_MAX_INPUT_TOKENS", min(32000, max_input_tokens),
        512, max_input_tokens,
    )
    rolling_summary_max_failures = bounded_int(
        "ROLLING_SUMMARY_MAX_FAILURES", 3, 1, 10
    )
    monitor_enabled = os.environ.get("MONITOR_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    token = os.environ.get("CHAT_API_TOKEN", "").strip()
    web_search_enabled = os.environ.get("WEB_SEARCH_ENABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    web_search_provider = (
        os.environ.get("WEB_SEARCH_PROVIDER", "tavily").strip().lower() or "tavily"
    )
    tavily_api_key = os.environ.get("TAVILY_API_KEY", "").strip() or None
    try:
        web_search_max_results = int(
            os.environ.get("WEB_SEARCH_MAX_RESULTS", "5").strip() or "5"
        )
    except ValueError:
        web_search_max_results = 5
    if web_search_max_results < 1:
        web_search_max_results = 1
    if web_search_max_results > 10:
        web_search_max_results = 10
    try:
        web_search_timeout_sec = int(
            os.environ.get("WEB_SEARCH_TIMEOUT_SEC", "15").strip() or "15"
        )
    except ValueError:
        web_search_timeout_sec = 15
    if web_search_timeout_sec < 3:
        web_search_timeout_sec = 3
    if web_search_timeout_sec > 60:
        web_search_timeout_sec = 60
    tron_full = os.environ.get("TRON_FULL_RPC", "").strip() or None
    tron_sol = os.environ.get("TRON_SOLIDITY_RPC", "").strip() or None
    eth_rpc = os.environ.get("ETHEREUM_JSONRPC_URL", "").strip() or None
    decode_script = os.environ.get("CONTRACT_DECODE_SCRIPT_PATH", "").strip() or None
    parser_cwd = os.environ.get("CONTRACT_PARSER_CWD", "").strip() or None
    try:
        decode_timeout = int(
            os.environ.get("CONTRACT_DECODE_TIMEOUT_SEC", "120").strip() or "120"
        )
    except ValueError:
        decode_timeout = 120
    if decode_timeout < 10:
        decode_timeout = 10
    if decode_timeout > 600:
        decode_timeout = 600
    memory_store_backend = (
        os.environ.get("MEMORY_STORE_BACKEND", "memory").strip().lower() or "memory"
    )
    memory_database_url = os.environ.get("MEMORY_DATABASE_URL", "").strip() or None
    memory_postgres_table = (
        os.environ.get("MEMORY_POSTGRES_TABLE", "agent_memories").strip()
        or "agent_memories"
    )
    memory_postgres_auto_create = os.environ.get(
        "MEMORY_POSTGRES_AUTO_CREATE", "0"
    ).strip().lower() in ("1", "true", "yes", "on")
    auth_database_url = (
        os.environ.get("AUTH_DATABASE_URL", "").strip() or memory_database_url or None
    )
    auth_users_table = (
        os.environ.get("AUTH_USERS_TABLE", "agent_users").strip() or "agent_users"
    )
    auth_postgres_auto_create = os.environ.get(
        "AUTH_POSTGRES_AUTO_CREATE", "0"
    ).strip().lower() in ("1", "true", "yes", "on")
    auth_token_secret = (
        os.environ.get("AUTH_TOKEN_SECRET", "").strip()
        or "chaincloud-local-dev-token-secret"
    )
    try:
        auth_token_expire_minutes = int(
            os.environ.get("AUTH_TOKEN_EXPIRE_MINUTES", "1440").strip() or "1440"
        )
    except ValueError:
        auth_token_expire_minutes = 1440
    if auth_token_expire_minutes < 5:
        auth_token_expire_minutes = 5
    if auth_token_expire_minutes > 43200:
        auth_token_expire_minutes = 43200
    monitor_database_url = os.environ.get("MONITOR_DATABASE_URL", "").strip() or auth_database_url
    monitor_transaction_database_url = os.environ.get("MONITOR_TRANSACTION_DATABASE_URL", "").strip() or ro or None
    return Settings(
        database_url=database_url,
        readonly_database_url=ro or None,
        readonly_clickhouse_host=ch_host,
        readonly_clickhouse_port=ch_port,
        readonly_clickhouse_user=ch_user,
        readonly_clickhouse_password=ch_password,
        readonly_clickhouse_database=ch_database,
        readonly_clickhouse_secure=ch_secure,
        clickhouse_datasources_path=clickhouse_datasources_path,
        agent_database_schema_path=agent_database_schema_path,
        agent_response_style_path=agent_response_style_path,
        agent_contract_decode_path=agent_contract_decode_path,
        openai_api_key=openai_api_key,
        openai_base_url=base or None,
        openai_model=model,
        openai_timeout_sec=openai_timeout_sec,
        openai_max_retries=openai_max_retries,
        model_context_window=model_context_window,
        max_input_tokens=max_input_tokens,
        reserved_output_tokens=reserved_output_tokens,
        tool_result_store_path=tool_result_store_path,
        tool_result_compression_threshold_bytes=tool_result_compression_threshold_bytes,
        tool_result_preview_chars=tool_result_preview_chars,
        rolling_summary_trigger_ratio=rolling_summary_trigger_ratio,
        rolling_summary_recent_messages=rolling_summary_recent_messages,
        rolling_summary_reactive_recent_messages=rolling_summary_reactive_recent_messages,
        rolling_summary_max_input_tokens=rolling_summary_max_input_tokens,
        rolling_summary_max_failures=rolling_summary_max_failures,
        chat_api_token=token or None,
        web_search_enabled=web_search_enabled,
        web_search_provider=web_search_provider,
        tavily_api_key=tavily_api_key,
        web_search_max_results=web_search_max_results,
        web_search_timeout_sec=web_search_timeout_sec,
        tron_full_rpc=tron_full,
        tron_solidity_rpc=tron_sol,
        ethereum_jsonrpc_url=eth_rpc,
        contract_decode_script_path=decode_script,
        contract_parser_cwd=parser_cwd,
        contract_decode_timeout_sec=decode_timeout,
        memory_store_backend=memory_store_backend,
        memory_database_url=memory_database_url,
        memory_postgres_table=memory_postgres_table,
        memory_postgres_auto_create=memory_postgres_auto_create,
        auth_database_url=auth_database_url,
        auth_users_table=auth_users_table,
        auth_postgres_auto_create=auth_postgres_auto_create,
        auth_token_secret=auth_token_secret,
        auth_token_expire_minutes=auth_token_expire_minutes,
        max_tool_retries=max_tool_retries,
        max_step_retries=max_step_retries,
        max_total_tool_calls=max_total_tool_calls,
        max_step_tool_calls=max_step_tool_calls,
        max_direct_tool_calls=max_direct_tool_calls,
        monitor_enabled=monitor_enabled,
        monitor_database_url=monitor_database_url,
        monitor_table_prefix=os.environ.get("MONITOR_TABLE_PREFIX", "monitor").strip() or "monitor",
        monitor_scan_interval_sec=bounded_int("MONITOR_SCAN_INTERVAL_SEC", 30, 5, 3600),
        monitor_transaction_database_url=monitor_transaction_database_url,
        monitor_transaction_table=os.environ.get("MONITOR_TRANSACTION_TABLE", "transactions").strip() or "transactions",
        monitor_transaction_columns=os.environ.get("MONITOR_TRANSACTION_COLUMNS", "").strip(),
        monitor_scan_batch_size=bounded_int("MONITOR_SCAN_BATCH_SIZE", 1000, 1, 10000),
        monitor_process_existing=os.environ.get("MONITOR_PROCESS_EXISTING", "0").strip().lower() in ("1", "true", "yes", "on"),
        planned_reviewer_low_model=planned_reviewer_low_model,
        planned_reviewer_high_model=planned_reviewer_high_model,
    )
