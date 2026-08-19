from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _fmt(value: Any, percent: bool = False) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.1f}%" if percent else f"{value:.2f}"


def write_report(
    payload: dict[str, Any], output_dir: str | Path, run_id: str
) -> tuple[Path, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / f"{run_id}.json", directory / f"{run_id}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    m, p = payload["metrics"]["overall"], payload["metrics"]["performance"]
    lines = [
        f"# Evaluation Report: {run_id}",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Task Success | {_fmt(m['task_success_rate'], True)} |",
        f"| Tool Selection Accuracy | {_fmt(m['tool_selection_accuracy'], True)} |",
        f"| Tool Argument Accuracy | {_fmt(m['tool_argument_accuracy'], True)} |",
        f"| Recovery Rate | {_fmt(m['recovery_success_rate'], True)} |",
        f"| Permission Accuracy | {_fmt(m['permission_gate_accuracy'], True)} |",
        "",
        "## Performance",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| P50 latency | {_fmt(p['latency_p50_ms'])} ms |",
        f"| P95 latency | {_fmt(p['latency_p95_ms'])} ms |",
        f"| Avg LLM Calls | {_fmt(p['avg_llm_calls'])} |",
        f"| Avg Tool Calls | {_fmt(p['avg_tool_calls'])} |",
        f"| Avg Tokens | {_fmt(p['avg_total_tokens'])} |",
        "",
        "## By category",
        "",
        "| Category | Cases | Success |",
        "|---|---:|---:|",
    ]
    for category, values in payload["metrics"]["by_category"].items():
        lines.append(
            f"| {category} | {values['cases']} | {_fmt(values['task_success_rate'], True)} |"
        )
    lines += [
        "",
        "## Cases",
        "",
        "| Case | Category | Outcome | Human review |",
        "|---|---|---|---|",
    ]
    for result in payload["results"]:
        lines.append(
            f"| {result['case_id']} | {result['category']} | {result['outcome']} | {'yes' if result['human_review_required'] else 'no'} |"
        )
    if payload.get("skipped_cases"):
        lines += ["", "## Skipped cases", "", "| Case | Reason |", "|---|---|"]
        for item in payload["skipped_cases"]:
            lines.append(f"| {item['case_id']} | {item['reason']} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
