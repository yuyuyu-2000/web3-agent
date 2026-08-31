from __future__ import annotations

import asyncio
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from chaincloud_agent_service.agent.answer_composer import acompose_final_answer

from chaincloud_agent_service.agent.answer_composer.complexity import (
    AnswerStyle,
    choose_answer_style,
)
from chaincloud_agent_service.agent.answer_composer.evidence import (
    EvidenceLevel,
    classify_tool_evidence_level,
)
from chaincloud_agent_service.agent.answer_composer.prompts import (
    ANSWER_COMPOSER_SYSTEM_PROMPT,
    LIGHTWEIGHT_ANSWER_COMPOSER_SYSTEM_PROMPT,
)
from chaincloud_agent_service.agent.answer_composer.renderer import (
    build_answer_context,
    build_fallback_answer,
)
from chaincloud_agent_service.agent.context_builder import ContextBuilder


def test_classify_tool_evidence_level() -> None:
    assert classify_tool_evidence_level("ethereum_rpc") == EvidenceLevel.ONCHAIN_RPC
    assert classify_tool_evidence_level("tron_full_rpc") == EvidenceLevel.ONCHAIN_RPC
    assert (
        classify_tool_evidence_level("postgres_memory_query")
        == EvidenceLevel.COMPANY_DATABASE
    )
    assert (
        classify_tool_evidence_level("clickhouse_aave_query")
        == EvidenceLevel.COMPANY_DATABASE
    )
    assert classify_tool_evidence_level("web_search") == EvidenceLevel.PUBLIC_SOURCE
    assert classify_tool_evidence_level("unknown") == EvidenceLevel.UNVERIFIED


def test_answer_composer_prompt_contains_required_sections() -> None:
    assert "核心结论" in ANSWER_COMPOSER_SYSTEM_PROMPT
    assert "证据等级" in ANSWER_COMPOSER_SYSTEM_PROMPT
    assert "链上 RPC 确认" in ANSWER_COMPOSER_SYSTEM_PROMPT
    assert "公司数据库确认" in ANSWER_COMPOSER_SYSTEM_PROMPT
    assert "公开资料支持" in ANSWER_COMPOSER_SYSTEM_PROMPT
    assert "不得把“公开资料称”写成“链上确认”" in ANSWER_COMPOSER_SYSTEM_PROMPT
    assert "不得使用 0x1234...abcd" in ANSWER_COMPOSER_SYSTEM_PROMPT
    assert (
        "不得新增上下文外的数字、地址、时间、机构、链接"
        in ANSWER_COMPOSER_SYSTEM_PROMPT
    )


def test_lightweight_prompt_avoids_full_report_sections() -> None:
    assert "不要输出完整六段式结构" in LIGHTWEIGHT_ANSWER_COMPOSER_SYSTEM_PROMPT
    assert "400 字以内" in LIGHTWEIGHT_ANSWER_COMPOSER_SYSTEM_PROMPT


def test_choose_answer_style_for_simple_question() -> None:
    messages = [
        HumanMessage(
            content="请介绍一下 ChainCloud-AI 当前支持哪些工具能力，回答要简洁。"
        ),
        AIMessage(content="ChainCloud-AI 支持数据库查询、RPC、合约解码、搜索和图表。"),
    ]

    assert choose_answer_style(messages) == AnswerStyle.LIGHTWEIGHT


def test_choose_answer_style_for_defi_evidence_question() -> None:
    messages = [
        HumanMessage(
            content="请分析 KelpDAO rsETH 事件对 Aave 的影响，并区分公开资料、公司数据库、链上 RPC 和模型推测。"
        ),
        ToolMessage(
            content='{"results": []}', name="web_search", tool_call_id="call-1"
        ),
        ToolMessage(
            content='{"rows": []}', name="postgres_aave_query", tool_call_id="call-2"
        ),
        AIMessage(content="这是原始分析。"),
    ]

    assert choose_answer_style(messages) == AnswerStyle.DETAILED


def test_build_answer_context_labels_tool_evidence() -> None:
    messages = [
        HumanMessage(content="分析 KelpDAO rsETH 事件对 Aave 的影响"),
        ToolMessage(
            content='{"rows": [{"asset": "rsETH", "amount": "53400"}]}',
            name="postgres_aave_query",
            tool_call_id="call-1",
        ),
        ToolMessage(
            content='{"results": [{"title": "incident report"}]}',
            name="web_search",
            tool_call_id="call-2",
        ),
        AIMessage(content="公开资料显示还有更多资金流需要验证。"),
    ]

    context = build_answer_context(messages)

    assert "## 用户问题" in context
    assert "postgres_aave_query" in context
    assert "公司数据库确认" in context
    assert "web_search" in context
    assert "公开资料支持" in context
    assert "公开资料显示还有更多资金流需要验证。" in context


def test_build_answer_context_keeps_safe_sql_args_and_redacts_secrets() -> None:
    tool_message = ToolMessage(
        content="[]", name="postgres_select", tool_call_id="call-1",
        additional_kwargs={
            "tool_result": {
                "tool_args": {
                    "sql": "SELECT * FROM public.justlend WHERE day = '2025-08-06'",
                    "api_key": "secret-value",
                }
            }
        },
    )

    context = build_answer_context([HumanMessage(content="查询"), tool_message])

    assert "调用参数" in context
    assert "public.justlend" in context
    assert "2025-08-06" in context
    assert "secret-value" not in context
    assert "[REDACTED]" in context


def test_build_fallback_answer_keeps_original_draft() -> None:
    fallback = build_fallback_answer("这是原始回答。")

    assert "## 核心结论" in fallback
    assert "## 详细分析" in fallback
    assert "这是原始回答。" in fallback
    assert "## 风险与待验证点" in fallback


def test_async_answer_composer_consumes_native_model_stream() -> None:
    class StreamingModel:
        async def astream(self, messages):  # type: ignore[no-untyped-def]
            assert messages
            yield AIMessageChunk(content="逐段")
            yield AIMessageChunk(content="生成答案")

    response = asyncio.run(
        acompose_final_answer(
            StreamingModel(),
            [HumanMessage(content="请回答"), AIMessage(content="原始答案")],
        )
    )

    assert response.content == "逐段生成答案"


def test_composer_context_keeps_execution_summary_evidence_and_provenance() -> None:
    execution_summary = (
        '{"step_results":[{"result_references":[{"result_id":"result-1",'
        '"evidence_source":"company_database","raw_result_location":'
        '"/tmp/result-1.json"}],"provenance":[{"result_id":"result-1"}]}]}'
    )
    evidence = ToolMessage(
        content='{"row_count":1,"sample":[{"value":42}]}',
        name="postgres_select",
        tool_call_id="call-1",
    )
    context = ContextBuilder("test").answer_composer(
        system_prompt="rules",
        current_request="query",
        execution_summary=execution_summary,
        evidence=[evidence],
        draft="",
    )
    rendered = "\n".join(str(message.content) for message in context.messages)

    assert "result-1" in rendered
    assert "company_database" in rendered
    assert "/tmp/result-1.json" in rendered
    assert "postgres_select" in rendered
    assert '"value":42' in rendered
    assert context.audit["category_tokens"]["draft"] == 0
