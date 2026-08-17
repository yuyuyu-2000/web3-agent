from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from chaincloud_agent_service.agent import graph as graph_module
from chaincloud_agent_service.agent.monitor_draft import (
    monitor_draft_hash,
    validate_monitor_draft,
)


class FakeDraftModel:
    create_payload = {
        "rule_type": "large_transaction",
        "address": None,
        "min_amount": None,
        "min_amount_usd": 100000,
        "chain": "TRON",
        "token": "USDT",
        "notification_channel": "feishu",
        "protocol": "JustLend",
    }

    def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
        pass

    def bind_tools(self, tools):  # type: ignore[no-untyped-def]
        return self

    def invoke(self, messages):  # type: ignore[no-untyped-def]
        system = str(messages[0].content) if messages else ""
        if "转换为 JSON" in system:
            return AIMessage(content=json.dumps(self.create_payload, ensure_ascii=False))
        if "按用户修订更新监控草稿" in system:
            return AIMessage(content='{"min_amount_usd":500000}')
        return AIMessage(content="普通回答")


def _settings():  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        openai_model="fake", openai_api_key="test", openai_base_url=None,
        openai_timeout_sec=10, openai_max_retries=0,
        agent_database_schema_path=None, agent_response_style_path=None,
        agent_contract_decode_path=None,
    )


def _compile(created: list[dict]):  # type: ignore[no-untyped-def]
    tool = StructuredTool.from_function(
        name="create_monitor_rule", description="create monitor rule",
        func=lambda **kwargs: json.dumps(kwargs),
    )

    def create_rule(draft, *, user_id):  # type: ignore[no-untyped-def]
        created.append({"draft": dict(draft), "user_id": user_id})
        return {"status": "success", "rule": {"rule_id": "rule-1"}}

    patches = (
        patch.object(graph_module, "ChatOpenAI", FakeDraftModel),
        patch.object(graph_module, "get_tools", lambda settings: [tool]),
        patch.object(graph_module, "create_monitor_rule_from_draft", create_rule),
    )
    for item in patches:
        item.start()
    try:
        return graph_module.compile_agent_graph(_settings(), MemorySaver()), patches
    except Exception:
        for item in reversed(patches):
            item.stop()
        raise


def _request(graph, thread: str, text: str, **extra):  # type: ignore[no-untyped-def]
    return asyncio.run(graph.ainvoke(
        {"messages": [HumanMessage(content=text)], "user_id": "user-1", **extra},
        config={"configurable": {"thread_id": thread}},
    ))


def test_draft_create_revise_checkpoint_and_confirm_exact_latest_version() -> None:
    created: list[dict] = []
    graph, patches = _compile(created)
    try:
        first = _request(graph, "draft-main", "帮我监控 JustLend 超过 10 万美元的 USDT 交易")
        assert first["monitor_draft_status"] == "awaiting_confirmation"
        assert first["monitor_draft_version"] == 1
        assert first["pending_monitor_draft"]["min_amount_usd"] == 100000
        assert created == []

        second = _request(graph, "draft-main", "改成 50 万，其他不变")
        assert second["monitor_draft_version"] == 2
        assert second["pending_monitor_draft"]["min_amount_usd"] == 500000
        assert second["pending_monitor_draft"]["token"] == "USDT"
        assert second["pending_monitor_draft"]["chain"] == "TRON"
        assert created == []

        snapshot = asyncio.run(graph.aget_state(
            {"configurable": {"thread_id": "draft-main"}}
        ))
        assert snapshot.values["pending_monitor_draft"]["protocol"] == "JustLend"

        stale = _request(
            graph, "draft-main", "确认", monitor_draft_requested_version=1,
            monitor_draft_requested_hash=monitor_draft_hash(first["pending_monitor_draft"], 1),
        )
        assert stale["monitor_draft_status"] == "awaiting_confirmation"
        assert created == []

        confirmed = _request(
            graph, "draft-main", "确认", monitor_draft_requested_version=2,
            monitor_draft_requested_hash=second["monitor_draft_hash"],
        )
        assert confirmed["monitor_draft_status"] == "confirmed"
        assert confirmed["pending_monitor_draft"] is None
        assert created == [{"draft": second["pending_monitor_draft"], "user_id": "user-1"}]
    finally:
        for item in reversed(patches):
            item.stop()


def test_cancel_does_not_create_formal_rule() -> None:
    created: list[dict] = []
    graph, patches = _compile(created)
    try:
        draft = _request(graph, "draft-cancel", "设置 JustLend 大额交易监控")
        cancelled = _request(
            graph, "draft-cancel", "取消",
            monitor_draft_requested_version=draft["monitor_draft_version"],
            monitor_draft_requested_hash=draft["monitor_draft_hash"],
        )
        assert cancelled["monitor_draft_status"] == "cancelled"
        assert cancelled["pending_monitor_draft"] is None
        assert created == []
    finally:
        for item in reversed(patches):
            item.stop()


def test_missing_fields_and_other_user_cannot_confirm() -> None:
    invalid = {
        "rule_type": "large_transaction", "address": None,
        "min_amount": None, "min_amount_usd": None,
        "chain": "TRON", "token": "USDT", "notification_channel": "feishu",
    }
    assert validate_monitor_draft(invalid)[0]["field"] == "amount_threshold"

    created: list[dict] = []
    graph, patches = _compile(created)
    try:
        draft = _request(graph, "draft-owner", "监控 JustLend 大额交易")
        result = asyncio.run(graph.ainvoke(
            {
                "messages": [HumanMessage(content="确认")], "user_id": "user-2",
                "monitor_draft_requested_version": draft["monitor_draft_version"],
                "monitor_draft_requested_hash": draft["monitor_draft_hash"],
            },
            config={"configurable": {"thread_id": "draft-owner"}},
        ))
        assert result["monitor_draft_status"] == "awaiting_confirmation"
        assert result["failure_reason"] == "草稿用户校验失败"
        assert created == []
    finally:
        for item in reversed(patches):
            item.stop()
