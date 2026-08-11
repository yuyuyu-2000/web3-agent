"""FastAPI 应用入口：挂载路由与生命周期，不包含业务编排逻辑。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from chaincloud_agent_service.agent.graph import compile_agent_graph
from chaincloud_agent_service.auth import AuthService, create_user_store
from chaincloud_agent_service.api.routes import auth as auth_routes
from chaincloud_agent_service.api.routes import chat as chat_routes
from chaincloud_agent_service.api.routes import memory as memory_routes
from chaincloud_agent_service.api.routes import scheduler as scheduler_routes
from chaincloud_agent_service.api.routes import tools as tools_routes
from chaincloud_agent_service.config import load_settings
from chaincloud_agent_service.memory import MemoryService, create_memory_store
from chaincloud_agent_service.persistence.checkpoint import (
    memory_checkpointer,
    postgres_checkpointer,
)
from chaincloud_agent_service.tools.scheduler_runtime import start_scheduler


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings() #加载配置
    if not settings.openai_api_key:
        raise RuntimeError("缺少环境变量 OPENAI_API_KEY")

    memory_store = create_memory_store(settings) #memory_store是底层存储
    memory_service = MemoryService(memory_store)
    memory_llm = ChatOpenAI(   #提供总结记忆功能
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
    
    #checkpoint：按 thread_id 自动保存聊天上下文。
    if settings.database_url:  #初始化会话checkpointer，数据库模式下使用postgres_checkpointer，内存模式下使用memory_checkpointer
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
            yield #配置 DATABASE_URL：会话保存到 PostgreSQL，重启后仍存在，也适合多进程部署。
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
        yield  #没有配置：保存到当前进程内存，服务重启后消失。


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
    return app


app = create_app()
