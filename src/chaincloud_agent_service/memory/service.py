from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from chaincloud_agent_service.memory.models import MemoryRecord
from chaincloud_agent_service.memory.store import InMemoryMemoryStore

DEFAULT_MAX_MESSAGES = 50
DEFAULT_TRANSCRIPT_MAX_CHARS = 12000

SUMMARY_PROMPT_TEMPLATE = """请把下面这段对话整理成一段可复用的长期记忆摘要。

要求：
1. 只保留对后续对话有持续价值的信息，例如用户偏好、项目背景、重要决策、待办事项和关键约束。
2. 不要逐轮复述聊天记录。
3. 不要编造对话中没有出现的信息。
4. 用简洁的中文分点或短段落输出。

对话内容：
{transcript}
"""

MEMORY_SYSTEM_PROMPT_TEMPLATE = """以下是该用户或该任务此前沉淀的长期记忆摘要。
请把它作为回答当前问题的背景参考，但不要生硬复述，也不要说“根据长期记忆”。

{summary}
"""


def message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def message_role(message: Any) -> str:
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        return "assistant"
    if isinstance(message, SystemMessage):
        return "system"
    if isinstance(message, ToolMessage):
        return "tool"
    role = getattr(message, "type", None) or getattr(message, "role", None)
    return str(role or message.__class__.__name__)


def truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if len(text) <= max_chars:
        return text

    marker = "\n...[truncated]\n"
    keep = max_chars - len(marker)
    if keep <= 0:
        return text[:max_chars]
    return text[:keep] + marker


class MemoryService:
    """Memory v1 的业务层。"""

    def __init__(self, store: InMemoryMemoryStore) -> None:
        self.store = store

    def save_memory(
        self,
        *,
        memory_key: str,
        summary: str,
        source_thread_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        return self.store.save(
            memory_key=memory_key,
            summary=summary,
            source_thread_id=source_thread_id,
            metadata=metadata,
        )

    def get_memory(self, memory_key: str) -> MemoryRecord | None:
        return self.store.get(memory_key)

    def list_memories(self) -> list[MemoryRecord]:
        return self.store.list()

    def delete_memory(self, memory_key: str) -> bool:
        return self.store.delete(memory_key)

    def build_transcript(
        self,
        messages: Sequence[Any],
        *,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        max_chars: int = DEFAULT_TRANSCRIPT_MAX_CHARS,
    ) -> str:
        if max_messages <= 0:
            raise ValueError("max_messages must be positive")

        recent_messages = list(messages)[-max_messages:]
        lines: list[str] = []

        for message in recent_messages:
            content = message_content_to_text(
                getattr(message, "content", message)
            ).strip()
            if not content:
                continue
            lines.append(f"{message_role(message)}: {content}")

        transcript = "\n".join(lines).strip()
        if not transcript:
            raise ValueError("cannot build memory transcript from empty messages")

        return truncate_text(transcript, max_chars)

    def build_summary_prompt(self, transcript: str) -> str:
        transcript = transcript.strip()
        if not transcript:
            raise ValueError("transcript must not be empty")

        return SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript)

    def build_memory_system_prompt_for_record(self, record: MemoryRecord) -> str:
        return MEMORY_SYSTEM_PROMPT_TEMPLATE.format(
            summary=record.summary.strip()
        ).strip()

    def build_memory_system_prompt(self, memory_key: str) -> str | None:
        record = self.get_memory(memory_key)
        if record is None:
            return None

        return self.build_memory_system_prompt_for_record(record)

    def build_memory_system_message_for_record(
        self, record: MemoryRecord
    ) -> SystemMessage:
        return SystemMessage(content=self.build_memory_system_prompt_for_record(record))

    def build_memory_system_message(self, memory_key: str) -> SystemMessage | None:
        record = self.get_memory(memory_key)
        if record is None:
            return None

        return self.build_memory_system_message_for_record(record)

    async def summarize_and_save(
        self,
        *,
        llm: Any,
        messages: Sequence[Any],
        memory_key: str,
        source_thread_id: str,
        metadata: dict[str, Any] | None = None,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        max_chars: int = DEFAULT_TRANSCRIPT_MAX_CHARS,
    ) -> MemoryRecord:
        transcript = self.build_transcript(
            messages,
            max_messages=max_messages,
            max_chars=max_chars,
        )
        prompt = self.build_summary_prompt(transcript)

        response = await llm.ainvoke([HumanMessage(content=prompt)])
        summary = message_content_to_text(
            getattr(response, "content", response)
        ).strip()

        if not summary:
            raise ValueError("llm returned empty memory summary")

        return self.save_memory(
            memory_key=memory_key,
            summary=summary,
            source_thread_id=source_thread_id,
            metadata=metadata,
        )
