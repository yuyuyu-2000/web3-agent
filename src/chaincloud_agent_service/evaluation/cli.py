from __future__ import annotations

import argparse
import json
import os

from langchain_openai import ChatOpenAI

from .ablation import run_ablation
from .adapters import HttpAgentAdapter, ReplayAdapter
from .judge import LangChainJudge
from .runner import EvaluationRunner, load_cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ChainCloud Agent evaluation")
    parser.add_argument("--dataset", default="eval/test_cases.jsonl")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--endpoint", help="Agent /chat endpoint")
    source.add_argument("--replay", help="Offline observations JSONL")
    parser.add_argument("--output-dir", default="eval_results")
    parser.add_argument("--token", default=os.getenv("CHAT_API_TOKEN"))
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument("--judge-model", help="Enable LLM judge for judge=true cases")
    args = parser.parse_args()
    adapter = (
        HttpAgentAdapter(args.endpoint, token=args.token)
        if args.endpoint
        else ReplayAdapter(args.replay)
    )
    judge = None
    if args.judge_model:
        judge = LangChainJudge(
            ChatOpenAI(
                model=args.judge_model,
                api_key=os.getenv("OPENAI_API_KEY"),
                base_url=os.getenv("OPENAI_BASE_URL") or None,
                max_retries=1,
            )
        )
    runner, cases = EvaluationRunner(adapter, judge=judge), load_cases(args.dataset)
    result = (
        run_ablation(runner, cases, args.output_dir)
        if args.ablation
        else runner.run(cases, output_dir=args.output_dir)
    )
    print(json.dumps(result.get("output_files", result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
