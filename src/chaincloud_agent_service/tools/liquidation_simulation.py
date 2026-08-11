"""Aave liquidation simulation chart tool migrated from chain_bot."""

from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import psycopg
from langchain_core.tools import StructuredTool
from psycopg.rows import dict_row

try:
    import plotly.graph_objects as go

    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False

ETH_COLLATERAL_RATIO_MIN = Decimal("0.45")
ETH_DEBT_RATIO_MIN = Decimal("0.45")
WETH_LIQ_BONUS = Decimal("1.05")
ETH_SYMBOLS = (
    "WETH",
    "ETH",
    "CBETH",
    "WSTETH",
    "RETH",
    "WEETH",
    "EZETH",
    "RSETH",
    "OSETH",
    "ETHX",
)
ETH_SYMBOLS_SQL = ", ".join(f"'{symbol}'" for symbol in ETH_SYMBOLS)


def _chart_dir() -> str:
    path = os.environ.get("CHART_DIR", "charts").strip() or "charts"
    os.makedirs(path, exist_ok=True)
    return path


def _save(fig, filename: str | None) -> dict[str, str]:
    safe_name = filename or f"liq_sim_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    if not safe_name.endswith(".html"):
        safe_name = f"{safe_name}.html"
    basename = os.path.basename(safe_name)
    path = os.path.join(_chart_dir(), basename)
    fig.write_html(path, include_plotlyjs="cdn")
    return {"filepath": os.path.abspath(path), "url": f"/charts/{basename}"}


def _fmt(value: Decimal, decimals: int = 2) -> str:
    quantize = Decimal(10) ** -decimals
    return str(value.quantize(quantize, rounding=ROUND_HALF_UP))


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _tables(protocol: str) -> tuple[str, str]:
    p = protocol.lower()
    suffix = "_borrow" if "borrow" in p else "_collateral"
    prefix = "aave_v2" if p in ("v2", "v2_borrow") else "aave_v3"
    return f"{prefix}_eth_positions{suffix}", f"{prefix}_eth_reserve_positions{suffix}"


def _fetch_rows(dsn: str, sql: str) -> list[dict]:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return conn.execute(sql).fetchall()


def _sql_all_liq_positions(pos_table: str, res_table: str, protocol: str) -> str:
    return f"""
WITH eth_cols AS (
    SELECT r.user_address,
           SUM(r.supply_raw / POWER(10, r.decimals::NUMERIC)) AS eth_col_amount,
           SUM(r.supply_usd) AS eth_col_usd
    FROM loan."{res_table}" r
    WHERE UPPER(r.symbol) IN ({ETH_SYMBOLS_SQL})
      AND r.use_as_collateral = true
      AND r.supply_raw > 0
    GROUP BY r.user_address
),
latest_snaps AS (
    SELECT DISTINCT ON (user_address) user_address, liq_price_weth
    FROM loan.position_snapshot_cache
    WHERE protocol = '{protocol}'
      AND liq_price_weth > 0
    ORDER BY user_address, block_timestamp DESC
)
SELECT COALESCE(e.eth_col_amount, 0) AS eth_collateral_amount,
       COALESCE(e.eth_col_usd, 0) AS eth_supply_usd_stored,
       p.total_collateral_usd,
       p.total_debt_usd,
       COALESCE(s.liq_price_weth, p.liq_price) AS liq_price
FROM loan."{pos_table}" p
LEFT JOIN eth_cols e ON e.user_address = p.user_address
LEFT JOIN latest_snaps s ON s.user_address = p.user_address
WHERE COALESCE(s.liq_price_weth, p.liq_price) > 0
  AND COALESCE(s.liq_price_weth, p.liq_price) < 9999999.99
"""


def _sql_short_liq_positions(pos_table: str) -> str:
    return f"""
SELECT (p.weth_debt_raw::numeric / POWER(10::numeric, 18)) AS weth_debt_amount,
       p.weth_debt_usd,
       p.total_collateral_usd,
       p.total_debt_usd,
       p.liq_price_rise
FROM loan."{pos_table}" p
WHERE p.liq_price_rise > 0
"""


def _simulate_liquidation_period(
    dsn: str,
    start_price,
    end_price,
    interval,
    protocol: str = "v3",
    eth_ratio: Decimal = ETH_COLLATERAL_RATIO_MIN,
    current_eth_price=None,
) -> list[dict]:
    start_price, end_price, interval = (
        _dec(start_price),
        _dec(end_price),
        _dec(interval),
    )
    current_eth_price = None if current_eth_price is None else _dec(current_eth_price)
    if start_price > end_price:
        raise ValueError("start 必须 <= end")
    if interval <= 0:
        raise ValueError("interval 必须 > 0")
    pos_table, res_table = _tables(protocol)
    rows = _fetch_rows(dsn, _sql_all_liq_positions(pos_table, res_table, protocol))
    positions = [
        (
            _dec(r["eth_collateral_amount"]),
            _dec(r["eth_supply_usd_stored"]),
            _dec(r["total_collateral_usd"]),
            _dec(r["total_debt_usd"]),
            _dec(r["liq_price"]),
        )
        for r in rows
        if current_eth_price is None or _dec(r["liq_price"]) <= current_eth_price
    ]
    result: list[dict] = []
    price = start_price
    while price <= end_price:
        total_eth_amount = total_eth_usd = total_liq_usd = Decimal("0")
        cnt = 0
        for eth_amt, eth_usd_stored, total_col_usd, debt_usd, liq_price in positions:
            if liq_price >= price:
                eth_usd = eth_amt * price
                if total_col_usd <= 0 or eth_usd_stored / total_col_usd < eth_ratio:
                    continue
                total_eth_amount += eth_amt
                total_eth_usd += eth_usd
                total_liq_usd += min(debt_usd * WETH_LIQ_BONUS, eth_usd)
                cnt += 1
        result.append(
            {
                "simulated_eth_price": _fmt(price),
                "positions_cnt": str(cnt),
                "total_eth_collateral_amount": _fmt(total_eth_amount, 6),
                "total_eth_collateral_usd": _fmt(total_eth_usd),
                "total_liquidation_usd": _fmt(total_liq_usd),
            }
        )
        price += interval
    return result


def _simulate_short_liquidation_period(
    dsn: str,
    start_price,
    end_price,
    interval,
    protocol: str = "v3_borrow",
    eth_ratio: Decimal = ETH_DEBT_RATIO_MIN,
) -> list[dict]:
    start_price, end_price, interval = (
        _dec(start_price),
        _dec(end_price),
        _dec(interval),
    )
    if start_price > end_price:
        raise ValueError("start 必须 <= end")
    if interval <= 0:
        raise ValueError("interval 必须 > 0")
    pos_table, _ = _tables(protocol)
    rows = _fetch_rows(dsn, _sql_short_liq_positions(pos_table))
    positions = [
        (
            _dec(r["weth_debt_amount"]),
            _dec(r["weth_debt_usd"]),
            _dec(r["total_collateral_usd"]),
            _dec(r["total_debt_usd"]),
            _dec(r["liq_price_rise"]),
        )
        for r in rows
    ]
    result: list[dict] = []
    price = start_price
    while price <= end_price:
        total_weth_amount = total_weth_usd = total_liq_usd = Decimal("0")
        cnt = 0
        for (
            weth_amt,
            weth_usd_stored,
            total_col_usd,
            total_debt_usd,
            liq_price_rise,
        ) in positions:
            if liq_price_rise <= price:
                if total_debt_usd <= 0 or weth_usd_stored / total_debt_usd < eth_ratio:
                    continue
                weth_usd_sim = weth_amt * price
                total_weth_amount += weth_amt
                total_weth_usd += weth_usd_sim
                total_liq_usd += min(weth_usd_sim * WETH_LIQ_BONUS, total_col_usd)
                cnt += 1
        result.append(
            {
                "simulated_eth_price": _fmt(price),
                "positions_cnt": str(cnt),
                "total_weth_debt_amount": _fmt(total_weth_amount, 6),
                "total_weth_debt_usd": _fmt(total_weth_usd),
                "total_liquidation_usd": _fmt(total_liq_usd),
            }
        )
        price += interval
    return result


def _merge_rows(
    base_rows: list[dict], incoming_rows: list[dict], side: str
) -> list[dict]:
    merged = {row["simulated_eth_price"]: dict(row) for row in base_rows}
    amount_key = (
        "total_eth_collateral_amount"
        if side == "collateral"
        else "total_weth_debt_amount"
    )
    usd_key = (
        "total_eth_collateral_usd" if side == "collateral" else "total_weth_debt_usd"
    )
    for row in incoming_rows:
        price_key = row["simulated_eth_price"]
        if price_key not in merged:
            merged[price_key] = dict(row)
            continue
        target = merged[price_key]
        target["positions_cnt"] = str(
            int(target.get("positions_cnt", "0")) + int(row.get("positions_cnt", "0"))
        )
        target["total_liquidation_usd"] = _fmt(
            _dec(target.get("total_liquidation_usd", "0"))
            + _dec(row.get("total_liquidation_usd", "0"))
        )
        target[amount_key] = _fmt(
            _dec(target.get(amount_key, "0")) + _dec(row.get(amount_key, "0")), 6
        )
        target[usd_key] = _fmt(
            _dec(target.get(usd_key, "0")) + _dec(row.get(usd_key, "0"))
        )
    return sorted(merged.values(), key=lambda item: _dec(item["simulated_eth_price"]))


def _simulate_liquidation_periods(
    dsn: str,
    side: str,
    protocol: str,
    start_price,
    end_price,
    interval,
    current_eth_price,
) -> list[dict]:
    protocol_variants = ["v2", "v3"] if protocol == "all" else [protocol]
    merged_rows: list[dict] = []
    for variant in protocol_variants:
        if side == "collateral":
            rows = _simulate_liquidation_period(
                dsn,
                start_price,
                end_price,
                interval,
                protocol=variant,
                current_eth_price=current_eth_price,
            )
        else:
            rows = _simulate_short_liquidation_period(
                dsn,
                start_price,
                end_price,
                interval,
                protocol=f"{variant}_borrow",
            )
        merged_rows = _merge_rows(merged_rows, rows, side)
    return merged_rows


def _get_current_eth_price_from_db(dsn: str) -> float:
    sql = """
        SELECT price
        FROM chain_data.binance_klines_1m_price
        WHERE symbol = 'ETH' AND quote_asset_symbol = 'USDT'
        ORDER BY open_time DESC
        LIMIT 1
    """
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute(sql).fetchone()
    if not row or row["price"] is None:
        raise ValueError("数据库中未查询到 ETH 当前价格")
    return float(row["price"])


def _build_liquidation_chart(
    dsn: str,
    side: str,
    protocol: str,
    start_pct: float,
    end_pct: float,
    title: str,
    filename: str | None = None,
) -> str:
    if not _HAS_PLOTLY:
        return json.dumps(
            {"error": "plotly 未安装，请安装项目依赖后重试"}, ensure_ascii=False
        )
    side = side.lower()
    if side not in ("collateral", "borrow"):
        return json.dumps(
            {"error": "side 只能是 collateral 或 borrow"}, ensure_ascii=False
        )
    protocol = protocol.lower()
    if protocol not in ("v2", "v3", "all"):
        return json.dumps(
            {"error": "protocol 只能是 v2、v3 或 all"}, ensure_ascii=False
        )

    try:
        eth_price = _get_current_eth_price_from_db(dsn)
    except Exception as exc:
        return json.dumps(
            {
                "error": f"无法获取 ETH 当前价格：{exc}",
                "hint": "请确认 chain_data.binance_klines_1m_price 可访问，且 READONLY_DATABASE_URL 正确。",
            },
            ensure_ascii=False,
        )

    start_price = max(0.01, eth_price * (1 + start_pct / 100.0))
    end_price = max(0.01, eth_price * (1 + end_pct / 100.0))
    if start_price > end_price:
        start_price, end_price = end_price, start_price
    step_usd = max(0.1, eth_price * 0.005)

    try:
        rows = _simulate_liquidation_periods(
            dsn,
            side=side,
            protocol=protocol,
            start_price=start_price,
            end_price=end_price,
            interval=step_usd,
            current_eth_price=eth_price,
        )
    except Exception as exc:
        return json.dumps(
            {
                "error": f"清算模拟失败：{exc}",
                "hint": "请检查 loan schema 下 aave_v2/v3 仓位表与 position_snapshot_cache 是否存在并有权限。",
            },
            ensure_ascii=False,
        )
    if not rows:
        return json.dumps({"error": "区间内未找到有效清算模拟数据"}, ensure_ascii=False)

    x_labels: list[str] = []
    y_usd: list[float] = []
    y_amount: list[float] = []
    for item in rows:
        sim_price = float(item["simulated_eth_price"])
        pct_diff = (sim_price / eth_price - 1) * 100
        x_labels.append(f"{pct_diff:+.1f}%")
        usd_val = float(item["total_liquidation_usd"])
        y_usd.append(usd_val)
        y_amount.append(usd_val / 1.05 / sim_price if sim_price > 0 else 0.0)

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=x_labels,
            y=y_usd,
            name="清算价值(USD)",
            marker_color="#334155",
            yaxis="y1",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_labels,
            y=y_amount,
            name="清算数量(ETH)",
            mode="lines+markers",
            line={"color": "#a78bfa", "width": 2},
            marker={"size": 4},
            yaxis="y2",
        )
    )
    fig.update_layout(
        title={"text": title, "font": {"size": 18}},
        template="plotly_dark",
        hovermode="x unified",
        xaxis={"title": "ETH 价格涨跌幅"},
        yaxis={"title": {"text": "清算价值(USD)", "font": {"color": "#64748b"}}},
        yaxis2={
            "title": {"text": "清算数量(ETH)", "font": {"color": "#a78bfa"}},
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
        },
        legend={"x": 0.01, "y": 0.99, "bgcolor": "rgba(0,0,0,0.5)"},
    )
    saved = _save(fig, filename)
    return json.dumps({"status": "success", **saved}, ensure_ascii=False)


def make_liquidation_simulation_tool(dsn: str) -> StructuredTool:
    def _invoke(
        side: str,
        protocol: str,
        start_pct: float,
        end_pct: float,
        title: str,
        filename: str | None = None,
    ) -> str:
        return _build_liquidation_chart(
            dsn=dsn,
            side=side,
            protocol=protocol,
            start_pct=start_pct,
            end_pct=end_pct,
            title=title,
            filename=filename,
        )

    return StructuredTool.from_function(
        name="generate_liquidation_simulation_chart",
        description=(
            "基于 Aave v2/v3 仓位数据做 ETH 涨跌幅区间的清算模拟，输出双轴图表 HTML。"
            "side=collateral 用于下跌清算，side=borrow 用于上涨清算。"
        ),
        func=_invoke,
    )
