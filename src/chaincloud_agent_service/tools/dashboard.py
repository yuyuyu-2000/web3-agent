"""HTML dashboard tool migrated from chain_bot."""

from __future__ import annotations

import html
import json
import os
from datetime import datetime

from langchain_core.tools import StructuredTool


def _chart_dir() -> str:
    path = os.environ.get("CHART_DIR", "charts").strip() or "charts"
    os.makedirs(path, exist_ok=True)
    return path


def create_dashboard(
    title: str,
    markdown_summary: str,
    chart_filepaths: list[str] | None = None,
) -> str:
    chart_filepaths = chart_filepaths or []
    safe_title = html.escape(title)
    safe_md = (
        markdown_summary.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    )
    frames_html = []
    for path in chart_filepaths:
        if os.path.exists(path):
            frames_html.append(
                f"""
                <section class="chart-container">
                  <iframe src="{html.escape(os.path.basename(path))}" width="100%" height="600" frameborder="0"></iframe>
                </section>
                """
            )
        else:
            frames_html.append(
                f"<p class='warning'>未找到图表文件 {html.escape(path)}</p>"
            )

    output_dir = _chart_dir()
    filename = f"dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    filepath = os.path.abspath(os.path.join(output_dir, filename))
    document = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>{safe_title}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      margin: 0;
      padding: 40px;
      line-height: 1.6;
    }}
    .header {{ margin-bottom: 30px; border-bottom: 1px solid #334155; padding-bottom: 20px; }}
    h1 {{ color: #f8fafc; font-size: 2.3em; margin: 0 0 10px; }}
    h2, h3, h4 {{ color: #cbd5e1; }}
    .summary-box {{ background: #1e293b; padding: 24px; margin-bottom: 36px; }}
    .summary-box table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
    .summary-box th, .summary-box td {{ border: 1px solid #334155; padding: 8px 14px; }}
    .summary-box th {{ background: #0f172a; color: #7dd3fc; }}
    .charts-grid {{ display: flex; flex-direction: column; gap: 30px; }}
    .chart-container {{ background: #1e293b; padding: 15px; }}
    .warning {{ color: #fca5a5; }}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</head>
<body>
  <div class="header">
    <h1>{safe_title}</h1>
    <p style="color:#94a3b8;">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
  </div>
  <div class="summary-box" id="md-summary"></div>
  <script>
    const mdContent = `{safe_md}`;
    document.getElementById('md-summary').innerHTML = marked.parse(mdContent);
  </script>
  <div class="charts-grid">
    {"".join(frames_html)}
  </div>
</body>
</html>
"""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(document)
        return json.dumps(
            {"status": "success", "filepath": filepath}, ensure_ascii=False
        )
    except Exception as e:
        return json.dumps({"error": f"生成 Dashboard 失败: {e}"}, ensure_ascii=False)


def make_dashboard_tool() -> StructuredTool:
    return StructuredTool.from_function(
        name="create_dashboard",
        description=(
            "把 Markdown 分析报告和多个本地 HTML 图表聚合成一个 Dashboard HTML 文件。"
            "chart_filepaths 应传 generate_*_chart 返回的 filepath 列表。"
        ),
        func=create_dashboard,
    )
