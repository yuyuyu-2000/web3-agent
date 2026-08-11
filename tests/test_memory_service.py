from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from chaincloud_agent_service.memory.service import MemoryService
from chaincloud_agent_service.memory.store import InMemoryMemoryStore


def make_service() -> MemoryService:
    return MemoryService(InMemoryMemoryStore())


def test_build_transcript_keeps_roles_and_content() -> None:
    service = make_service()

    transcript = service.build_transcript(
        [
            SystemMessage(content="你是一个项目助手。"),
            HumanMessage(content="我正在做 ChainCloud Memory 重构。"),
            AIMessage(content="可以先做 store 和 service。"),
            ToolMessage(content="tool result", tool_call_id="call-1"),
        ]
    )

    assert "system: 你是一个项目助手。" in transcript
    assert "user: 我正在做 ChainCloud Memory 重构。" in transcript
    assert "assistant: 可以先做 store 和 service。" in transcript
    assert "tool: tool result" in transcript


def test_build_transcript_uses_recent_messages_only() -> None:
    service = make_service()
    messages = [HumanMessage(content=f"message-{idx}") for idx in range(5)]

    transcript = service.build_transcript(messages, max_messages=2)

    assert "message-0" not in transcript
    assert "message-1" not in transcript
    assert "message-2" not in transcript
    assert "message-3" in transcript
    assert "message-4" in transcript


def test_build_transcript_rejects_empty_messages() -> None:
    service = make_service()

    with pytest.raises(ValueError):
        service.build_transcript([])

    with pytest.raises(ValueError):
        service.build_transcript([HumanMessage(content="   ")])


def test_build_summary_prompt_contains_transcript_and_instructions() -> None:
    service = make_service()

    prompt = service.build_summary_prompt("user: 我希望回答更适合工程实践。")

    assert "长期记忆摘要" in prompt
    assert "用户偏好" in prompt
    assert "user: 我希望回答更适合工程实践。" in prompt


def test_build_memory_system_message_returns_none_when_missing() -> None:
    service = make_service()

    assert service.build_memory_system_message("missing") is None


def test_build_memory_system_message_from_saved_record() -> None:
    service = make_service()
    service.save_memory(
        memory_key="chaincloud-memory",
        summary="用户正在按小步提交方式重构 ChainCloud Memory v1。",
        source_thread_id="thread-1",
    )

    message = service.build_memory_system_message("chaincloud-memory")

    assert isinstance(message, SystemMessage)
    assert "长期记忆摘要" in str(message.content)
    assert "ChainCloud Memory v1" in str(message.content)


def test_summarize_and_save_calls_llm_and_persists_record() -> None:
    class FakeLLM:
        def __init__(self) -> None:
            self.last_messages = None

        async def ainvoke(self, messages):  # type: ignore[no-untyped-def]
            self.last_messages = messages
            return AIMessage(content="用户正在实现 Memory v1 的 service 层。")

    service = make_service()
    llm = FakeLLM()

    record = asyncio.run(
        service.summarize_and_save(
            llm=llm,
            messages=[HumanMessage(content="我们继续做 memory/service.py")],
            memory_key="chaincloud-memory",
            source_thread_id="thread-1",
            metadata={"stage": "service"},
        )
    )

    assert record.memory_key == "chaincloud-memory"
    assert record.summary == "用户正在实现 Memory v1 的 service 层。"
    assert record.metadata == {"stage": "service"}
    assert service.get_memory("chaincloud-memory") == record
    assert llm.last_messages is not None
    assert "memory/service.py" in str(llm.last_messages[0].content)
