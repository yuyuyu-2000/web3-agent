"""FastAPI 应用入口：挂载路由与生命周期，不包含业务编排逻辑。"""

from __future__ import annotations

import os
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from chaincloud_agent_service.agent.graph import compile_agent_graph
from chaincloud_agent_service.auth import AuthService, create_user_store
from chaincloud_agent_service.api.routes import auth as auth_routes
from chaincloud_agent_service.api.routes import chat as chat_routes
from chaincloud_agent_service.api.routes import memory as memory_routes
from chaincloud_agent_service.api.routes import scheduler as scheduler_routes
from chaincloud_agent_service.api.routes import tools as tools_routes
from chaincloud_agent_service.api.routes import monitoring as monitoring_routes
from chaincloud_agent_service.config import load_settings
from chaincloud_agent_service.memory import MemoryService, create_memory_store
from chaincloud_agent_service.persistence.checkpoint import (
    memory_checkpointer,
    postgres_checkpointer,
)
from chaincloud_agent_service.tools.scheduler_runtime import start_scheduler
from chaincloud_agent_service.tools.scheduler_runtime import add_monitor_scan_job
from chaincloud_agent_service.monitoring import (
    MonitorStore,
    MonitorWorker,
    PostgresTransactionSource,
)
from chaincloud_agent_service.monitoring.runtime import configure_monitor_store
from chaincloud_agent_service.notification import FeishuNotifier, NotificationService


def _install_app_state(
    app: FastAPI,
    *,
    settings,
    graph,
    memory_service: MemoryService,
    memory_llm: ChatOpenAI,
    auth_service: AuthService,
) -> None:
    app.state.settings = settings
    app.state.graph = graph
    app.state.memory_service = memory_service
    app.state.memory_llm = memory_llm
    app.state.auth_service = auth_service


def _configure_monitoring(app: FastAPI, settings) -> None:
    if not settings.monitor_enabled:
        configure_monitor_store(None)
        return
    if (
        not settings.monitor_database_url
        or not settings.monitor_transaction_database_url
    ):
        raise RuntimeError(
            "MONITOR_ENABLED requires monitor and transaction database URLs"
        )
    store = MonitorStore(
        settings.monitor_database_url, prefix=settings.monitor_table_prefix
    )
    store.ensure_schema()
    configure_monitor_store(store)
    default_columns = {
        "id": "id",
        "hash": "transaction_hash",
        "from_address": "from_address",
        "to_address": "to_address",
        "amount": "amount",
        "amount_usd": "amount_usd",
        "chain": "chain",
        "token": "token",
        "occurred_at": "created_at",
    }
    if settings.monitor_transaction_columns:
        default_columns.update(json.loads(settings.monitor_transaction_columns))
    source = PostgresTransactionSource(
        settings.monitor_transaction_database_url,
        table=settings.monitor_transaction_table,
        columns=default_columns,
        batch_size=settings.monitor_scan_batch_size,
        process_existing_on_first_run=settings.monitor_process_existing,
    )
    notifications = NotificationService({"feishu": FeishuNotifier()})

    def destination_for_user(user_id: str, channel: str) -> str | None:
        return store.notification_destination(user_id, channel)

    worker = MonitorWorker(store, source, notifications, destination_for_user)
    app.state.monitor_store = store
    app.state.monitor_worker = worker
    add_monitor_scan_job(worker.run_once, settings.monitor_scan_interval_sec)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()  # 加载配置
    if not settings.openai_api_key:
        raise RuntimeError("缺少环境变量 OPENAI_API_KEY")

    memory_store = create_memory_store(settings)  # memory_store是底层存储
    if settings.memory_recall_enabled and hasattr(
        memory_store, "migrate_for_semantic_recall"
    ):
        try:
            memory_store.migrate_for_semantic_recall()
        except Exception:
            pass
    embedding_provider = (
        OpenAIEmbeddings(
            model=settings.memory_embedding_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
        if settings.memory_recall_enabled
        else None
    )
    memory_service = MemoryService(memory_store, embedding_provider=embedding_provider)
    memory_llm = ChatOpenAI(  # 提供总结记忆功能
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.openai_timeout_sec,
        max_retries=settings.openai_max_retries,
    )

    auth_store = create_user_store(settings)
    auth_service = AuthService(
        auth_store,
        token_secret=settings.auth_token_secret,
        token_expire_minutes=settings.auth_token_expire_minutes,
    )

    async def _scheduled_executor(prompt: str, task_id: str) -> str:
        graph = app.state.graph
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=prompt)]},
            config={"configurable": {"thread_id": f"scheduled:{task_id}"}},
        )
        messages = result.get("messages", [])
        if not messages:
            return ""
        content = getattr(messages[-1], "content", "")
        if isinstance(content, str):
            return content
        return str(content)

    # checkpoint：按 thread_id 自动保存聊天上下文。
    if settings.database_url:  # 初始化会话checkpointer，数据库模式下使用postgres_checkpointer，内存模式下使用memory_checkpointer
        async with postgres_checkpointer(settings.database_url) as checkpointer:
            graph = compile_agent_graph(settings, checkpointer)
            _install_app_state(
                app,
                settings=settings,
                graph=graph,
                memory_service=memory_service,
                memory_llm=memory_llm,
                auth_service=auth_service,
            )
            start_scheduler(_scheduled_executor)
            _configure_monitoring(app, settings)
            yield  # 配置 DATABASE_URL：会话保存到 PostgreSQL，重启后仍存在，也适合多进程部署。
    else:
        checkpointer = memory_checkpointer()
        graph = compile_agent_graph(settings, checkpointer)
        _install_app_state(
            app,
            settings=settings,
            graph=graph,
            memory_service=memory_service,
            memory_llm=memory_llm,
            auth_service=auth_service,
        )
        start_scheduler(_scheduled_executor)
        _configure_monitoring(app, settings)
        yield  # 没有配置：保存到当前进程内存，服务重启后消失。


def create_app() -> FastAPI:
    app = FastAPI(title="ChainCloud AI Agent", lifespan=lifespan)
    chart_dir = os.environ.get("CHART_DIR", "charts").strip() or "charts"
    os.makedirs(chart_dir, exist_ok=True)
    app.mount("/charts", StaticFiles(directory=chart_dir), name="charts")
    app.include_router(auth_routes.router, tags=["auth"])
    app.include_router(chat_routes.router, tags=["chat"])
    app.include_router(memory_routes.router, tags=["memory"])
    app.include_router(scheduler_routes.router, tags=["scheduler"])
    app.include_router(tools_routes.router, tags=["tools"])
    app.include_router(monitoring_routes.router, tags=["monitoring"])
    return app


app = create_app()
