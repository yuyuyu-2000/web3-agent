from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters import AgentAdapter
from .deterministic import evaluate_case
from .metrics import aggregate
from .models import EvalCase
from .report import write_report


def load_cases(path: str | Path) -> list[EvalCase]:
    with Path(path).open(encoding="utf-8") as handle:
        return [EvalCase.model_validate_json(line) for line in handle if line.strip()]


class EvaluationRunner:
    def __init__(self, adapter: AgentAdapter, *, judge: Any = None) -> None:
        self.adapter, self.judge = adapter, judge

    def run(
        self,
        cases: list[EvalCase],
        *,
        output_dir: str | Path = "eval_results",
        run_id: str | None = None,
        variant: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        case_results = []
        for case in cases:
            result = evaluate_case(case, self.adapter.run(case, variant=variant))
            if case.ground_truth.judge and self.judge is not None:
                result.judge = self.judge.judge(case, result.observation)
                if result.judge.get("passed") is False:
                    result.deterministic_passed = False
                    result.outcome = "failed"
            case_results.append(result)
        run_id = run_id or datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
        payload = {
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "variant": variant or {},
            "metrics": aggregate(case_results),
            "results": [r.model_dump() for r in case_results],
        }
        json_path, md_path = write_report(payload, output_dir, run_id)
        payload["output_files"] = [str(json_path), str(md_path)]
        return payload
