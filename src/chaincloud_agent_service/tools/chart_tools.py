"""Plotly chart tools migrated from chain_bot."""

from __future__ import annotations

import json
import os
from datetime import datetime

from langchain_core.tools import StructuredTool

from chaincloud_agent_service.tools.liquidation_simulation import (
    make_liquidation_simulation_tool,
)
from chaincloud_agent_service.tools.query_state import get_last_query_result

try:
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go

    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False


def _chart_dir() -> str:
    path = os.environ.get("CHART_DIR", "charts").strip() or "charts"
    os.makedirs(path, exist_ok=True)
    return path


def _save(fig, filename: str | None, prefix: str) -> dict[str, str]:
    safe_name = filename or f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    if not safe_name.endswith(".html"):
        safe_name = f"{safe_name}.html"
    basename = os.path.basename(safe_name)
    path = os.path.join(_chart_dir(), basename)
    fig.write_html(path, include_plotlyjs="cdn")
    return {"filepath": os.path.abspath(path), "url": f"/charts/{basename}"}


def _safe_float(value) -> float:
    if value in (None, "None", "", "null", "NaN"):
        return 0.0
    try:
        if isinstance(value, str):
            value = value.strip().replace(",", "")
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _require_plotly() -> str | None:
    if _HAS_PLOTLY:
        return None
    return json.dumps(
        {"error": "plotly/pandas 未安装，请安装项目依赖后再生成图表"},
        ensure_ascii=False,
    )


def generate_bar_chart(
    labels: list,
    values: list,
    title: str = "柱状图",
    xlabel: str = "类别",
    ylabel: str = "数值",
    filename: str | None = None,
    use_log_scale: bool = False,
) -> str:
    if error := _require_plotly():
        return error
    numeric_values = [_safe_float(v) for v in values]
    fig = go.Figure(
        go.Bar(
            x=[str(label) for label in labels],
            y=numeric_values,
            text=[f"{v:,.2f}" for v in numeric_values],
            textposition="outside",
            marker_color="steelblue",
        )
    )
    fig.update_layout(
        title={"text": title, "font": {"size": 18}},
        xaxis_title=xlabel,
        yaxis_title=ylabel,
        yaxis_type="log" if use_log_scale else "linear",
        template="plotly_dark",
        hovermode="x unified",
    )
    saved = _save(fig, filename, "bar")
    return json.dumps({"status": "success", **saved}, ensure_ascii=False)


def generate_time_series(
    dates: list,
    values: list,
    title: str = "时间序列",
    ylabel: str = "数值",
    filename: str | None = None,
    use_log_scale: bool = False,
) -> str:
    if error := _require_plotly():
        return error
    df = pd.DataFrame(
        {"date": pd.to_datetime(dates), "value": [_safe_float(v) for v in values]}
    ).sort_values("date")
    if len(df) >= 2:
        full_range = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
        df = (
            df.set_index("date")
            .reindex(full_range, fill_value=0)
            .rename_axis("date")
            .reset_index()
        )
    fig = px.line(
        df, x="date", y="value", title=title, markers=True, template="plotly_dark"
    )
    fig.update_layout(
        yaxis_title=ylabel,
        yaxis_type="log" if use_log_scale else "linear",
        hovermode="x unified",
    )
    saved = _save(fig, filename, "ts")
    return json.dumps({"status": "success", **saved}, ensure_ascii=False)


def generate_pie_chart(
    labels: list,
    values: list,
    title: str = "饼图",
    filename: str | None = None,
) -> str:
    if error := _require_plotly():
        return error
    fig = go.Figure(
        go.Pie(labels=labels, values=[_safe_float(v) for v in values], hole=0.3)
    )
    fig.update_layout(
        title={"text": title, "font": {"size": 18}}, template="plotly_dark"
    )
    saved = _save(fig, filename, "pie")
    return json.dumps({"status": "success", **saved}, ensure_ascii=False)


def _rows_to_series(
    rows: list[dict],
    date_col: str,
    y_cols: list[str],
    y_labels: list[str] | None,
) -> tuple[list[str], dict[str, list[float]]]:
    if not rows:
        raise ValueError("没有可用的数据库查询结果，请先调用 postgres_select")
    available_cols = list(rows[0].keys())
    missing = [col for col in [date_col, *y_cols] if col not in available_cols]
    if missing:
        raise ValueError(f"列名不存在: {missing}; 可选列: {available_cols}")
    dates = [str(row.get(date_col, "")) for row in rows]
    series = {}
    for idx, col in enumerate(y_cols):
        label = y_labels[idx] if y_labels and idx < len(y_labels) else col
        series[label] = [_safe_float(row.get(col, 0)) for row in rows]
    return dates, series


def generate_multi_line_chart(
    dates: list | None = None,
    series: dict | None = None,
    date_col: str | None = None,
    y_cols: list | None = None,
    y_labels: list | None = None,
    title: str = "多线折线图",
    ylabel: str = "数值",
    filename: str | None = None,
) -> str:
    if error := _require_plotly():
        return error
    try:
        if date_col and y_cols:
            dates, series = _rows_to_series(
                get_last_query_result(),
                date_col,
                [str(col) for col in y_cols],
                y_labels,
            )
        if dates is None or series is None:
            return json.dumps(
                {"error": "必须提供 (dates, series) 或 (date_col, y_cols)"},
                ensure_ascii=False,
            )
        data = {"date": pd.to_datetime(dates)}
        for name, vals in series.items():
            data[str(name)] = [_safe_float(v) for v in vals]
        df = pd.DataFrame(data).sort_values("date")
        if len(df) >= 2:
            full_range = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
            df = (
                df.set_index("date")
                .reindex(full_range, fill_value=0)
                .rename_axis("date")
                .reset_index()
            )
        fig = go.Figure()
        colors = ["#4FC3F7", "#FFB74D", "#81C784", "#BA68C8", "#FF8A65", "#A1887F"]
        for idx, name in enumerate(series.keys()):
            fig.add_trace(
                go.Scatter(
                    x=df["date"],
                    y=df[str(name)],
                    mode="lines+markers",
                    name=str(name),
                    line={"width": 2, "color": colors[idx % len(colors)]},
                    marker={"size": 5},
                )
            )
        fig.update_layout(
            title={"text": title, "font": {"size": 18}},
            yaxis_title=ylabel,
            template="plotly_dark",
            hovermode="x unified",
            legend={"x": 0.01, "y": 0.99, "bgcolor": "rgba(0,0,0,0.3)"},
        )
        saved = _save(fig, filename, "multi")
        return json.dumps({"status": "success", **saved}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def generate_dual_axis_chart(
    dates: list | None = None,
    y1_values: list | None = None,
    y2_values: list | None = None,
    date_col: str | None = None,
    y1_col: str | None = None,
    y2_col: str | None = None,
    title: str = "双轴对比趋势",
    y1_label: str = "指标1",
    y2_label: str = "指标2",
    filename: str | None = None,
) -> str:
    if error := _require_plotly():
        return error
    try:
        if date_col and y1_col and y2_col:
            rows = get_last_query_result()
            if not rows:
                raise ValueError("没有可用的数据库查询结果，请先调用 postgres_select")
            available_cols = list(rows[0].keys())
            missing = [
                col for col in (date_col, y1_col, y2_col) if col not in available_cols
            ]
            if missing:
                raise ValueError(f"列名不存在: {missing}; 可选列: {available_cols}")
            dates = [str(row.get(date_col, "")) for row in rows]
            y1_values = [_safe_float(row.get(y1_col, 0)) for row in rows]
            y2_values = [_safe_float(row.get(y2_col, 0)) for row in rows]
        if dates is None or y1_values is None or y2_values is None:
            return json.dumps(
                {
                    "error": "必须提供 (dates, y1_values, y2_values) 或 (date_col, y1_col, y2_col)"
                },
                ensure_ascii=False,
            )
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(dates),
                "y1": [_safe_float(v) for v in y1_values],
                "y2": [_safe_float(v) for v in y2_values],
            }
        ).sort_values("date")
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["y1"],
                name=y1_label,
                mode="lines+markers",
                line={"color": "#4FC3F7", "width": 2},
                yaxis="y1",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=df["date"],
                y=df["y2"],
                name=y2_label,
                mode="lines+markers",
                line={"color": "#FFB74D", "width": 2, "dash": "dot"},
                yaxis="y2",
            )
        )
        fig.update_layout(
            title={"text": title, "font": {"size": 18}},
            template="plotly_dark",
            hovermode="x unified",
            yaxis={"title": {"text": y1_label, "font": {"color": "#4FC3F7"}}},
            yaxis2={
                "title": {"text": y2_label, "font": {"color": "#FFB74D"}},
                "overlaying": "y",
                "side": "right",
                "showgrid": False,
            },
            legend={"x": 0.01, "y": 0.99},
        )
        saved = _save(fig, filename, "dual")
        return json.dumps({"status": "success", **saved}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def generate_price_distribution_chart(
    x_values: list | None = None,
    y1_values: list | None = None,
    y2_values: list | None = None,
    x_col: str | None = None,
    y1_col: str | None = None,
    y2_col: str | None = None,
    title: str = "价格区间分布图",
    x_label: str = "价格",
    y1_label: str = "价值",
    y2_label: str = "数量",
    filename: str | None = None,
) -> str:
    if error := _require_plotly():
        return error
    try:
        if x_col and y1_col and y2_col:
            rows = get_last_query_result()
            if not rows:
                raise ValueError("没有可用的数据库查询结果，请先调用 postgres_select")
            available_cols = list(rows[0].keys())
            missing = [
                col for col in (x_col, y1_col, y2_col) if col not in available_cols
            ]
            if missing:
                raise ValueError(f"列名不存在: {missing}; 可选列: {available_cols}")
            x_values = [_safe_float(row.get(x_col, 0)) for row in rows]
            y1_values = [_safe_float(row.get(y1_col, 0)) for row in rows]
            y2_values = [_safe_float(row.get(y2_col, 0)) for row in rows]
        if x_values is None or y1_values is None or y2_values is None:
            return json.dumps({"error": "必须提供数值数组或列名"}, ensure_ascii=False)
        df = pd.DataFrame(
            {
                "x": [_safe_float(v) for v in x_values],
                "y1": [_safe_float(v) for v in y1_values],
                "y2": [_safe_float(v) for v in y2_values],
            }
        ).sort_values("x")
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=df["x"],
                y=df["y1"],
                name=y1_label,
                marker={"color": "rgba(255, 183, 77, 0.7)"},
                yaxis="y1",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=df["x"],
                y=df["y2"],
                name=y2_label,
                mode="lines+markers",
                line={"color": "#4FC3F7", "width": 3},
                yaxis="y2",
            )
        )
        fig.update_layout(
            title={"text": title, "font": {"size": 18}},
            template="plotly_dark",
            hovermode="x unified",
            xaxis={"title": x_label},
            yaxis={"title": {"text": y1_label}},
            yaxis2={"title": {"text": y2_label}, "overlaying": "y", "side": "right"},
            legend={"x": 0.01, "y": 0.99, "bgcolor": "rgba(0,0,0,0.5)"},
        )
        saved = _save(fig, filename, "dist")
        return json.dumps({"status": "success", **saved}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def make_chart_tools(readonly_database_url: str | None = None) -> list[StructuredTool]:
    tools: list[StructuredTool] = [
        StructuredTool.from_function(
            name="generate_bar_chart",
            description="生成交互式柱状图 HTML，适合类别比较。",
            func=generate_bar_chart,
        ),
        StructuredTool.from_function(
            name="generate_time_series",
            description="生成交互式时间序列折线图 HTML，适合按天或按月展示趋势。",
            func=generate_time_series,
        ),
        StructuredTool.from_function(
            name="generate_pie_chart",
            description="生成交互式饼图 HTML，适合展示占比分布。",
            func=generate_pie_chart,
        ),
        StructuredTool.from_function(
            name="generate_multi_line_chart",
            description=(
                "生成多线折线图 HTML。可传 dates+series，也可先调用 postgres_select，"
                "再传 date_col+y_cols 从最近查询结果取数。"
            ),
            func=generate_multi_line_chart,
        ),
        StructuredTool.from_function(
            name="generate_dual_axis_chart",
            description=(
                "生成双 Y 轴折线图 HTML。可传数组，也可先调用 postgres_select，"
                "再传 date_col+y1_col+y2_col 从最近查询结果取数。"
            ),
            func=generate_dual_axis_chart,
        ),
        StructuredTool.from_function(
            name="generate_price_distribution_chart",
            description=(
                "生成价格区间/数值区间分布双轴图 HTML。可传数组，也可先调用 postgres_select，"
                "再传 x_col+y1_col+y2_col 从最近查询结果取数。"
            ),
            func=generate_price_distribution_chart,
        ),
    ]
    if readonly_database_url:
        tools.append(make_liquidation_simulation_tool(readonly_database_url))
    return tools
