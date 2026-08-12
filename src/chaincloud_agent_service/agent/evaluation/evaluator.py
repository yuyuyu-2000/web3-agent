from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from chaincloud_agent_service.agent.evaluation.models import EvaluationDecision
from chaincloud_agent_service.agent.planning.models import PlanStep, StepResult


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

EVALUATOR_SYSTEM_PROMPT = """你是任务步骤 Evaluator。判断执行结果是否满足步骤成功标准。
只输出 JSON：
{"action":"pass|retry|replan|partial|fail","reason":"原因","feedback":"给执行器或规划器的改进要求","confidence":0到1}

规则：
- pass：结果和证据足以满足成功标准。
- retry：目标仍合理，但应修正工具参数或补充查询。
- replan：当前步骤或后续路径已不适用，需要重新规划剩余任务。
- partial：已有可靠结果，但受数据、预算或外部限制无法完全完成。
- fail：没有可靠结果且继续执行无意义。
- 工具成功不等于步骤成功，必须逐项核对 success_criteria。
- 网络瞬时错误由 ToolNode 处理，不要因此要求机械重试；retry 只用于步骤目标未满足且可通过语义修复继续。
- candidate_result.error 表示仍未解决的工具失败。关键步骤无 fallback 时必须 fail；非关键步骤可 partial。
- 权限或 guardrail 拒绝不得建议通过等价工具绕过。
不要补充结果中不存在的事实，不要回答用户问题。
"""


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def _parse_decision(text: str) -> EvaluationDecision:
    fenced = _JSON_FENCE_RE.search(text)
    candidate = fenced.group(1).strip() if fenced else text.strip()
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    return EvaluationDecision.model_validate(json.loads(candidate))


def evaluate_step(
    model: Any,
    step: PlanStep,
    result: StepResult,
) -> EvaluationDecision:
    """Evaluate a candidate result; degrade safely if the evaluator is unavailable."""

    if not result.summary.strip():
        return EvaluationDecision(
            action="retry",
            reason="步骤没有生成有效结果",
            feedback="重新执行当前步骤并提供明确结果",
            confidence=1.0,
        )
    prompt = json.dumps(
        {
            "step": step.model_dump(),
            "candidate_result": result.model_dump(),
        },
        ensure_ascii=False,
    )
    try:
        response = model.invoke(
            [
                SystemMessage(content=EVALUATOR_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        return _parse_decision(_message_text(response))
    except Exception:
        return EvaluationDecision(
            action="pass",
            reason="Evaluator 不可用，保留原有的非空结果判定",
            feedback="",
            confidence=0.0,
        )
