from __future__ import annotations

import json
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage

from .models import EvalCase, EvalObservation

JUDGE_PROMPT = """You are an offline evaluation judge. Judge only whether the candidate answer satisfies the rubric. Tool/permission/safety execution is checked separately. Return JSON only: {\"passed\":true|false,\"score\":0..1,\"reason\":\"brief evidence-based reason\"}. Never add facts."""


class Judge(Protocol):
    def judge(self, case: EvalCase, observation: EvalObservation) -> dict[str, Any]: ...


class LangChainJudge:
    """Judge model is invoked after Agent completion and receives no Agent credentials/tools."""

    def __init__(self, model: Any) -> None:
        self.model = model

    def judge(self, case: EvalCase, observation: EvalObservation) -> dict[str, Any]:
        rubric = {
            "user_query": case.user_query,
            "expected_result": case.ground_truth.expected_result,
            "required_facts": case.ground_truth.required_facts,
            "candidate_answer": observation.reply,
        }
        response = self.model.invoke(
            [
                SystemMessage(content=JUDGE_PROMPT),
                HumanMessage(content=json.dumps(rubric, ensure_ascii=False)),
            ]
        )
        return json.loads(
            str(getattr(response, "content", response))
            .strip()
            .removeprefix("```json")
            .removesuffix("```")
            .strip()
        )
