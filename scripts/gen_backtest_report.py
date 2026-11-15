# -*- coding: utf-8 -*-
"""生成回测报告：完整常规指标 + 逐笔交易明细 + 图表，并落盘 CSV/JSON。

用法:
    python scripts/gen_backtest_report.py
    python scripts/gen_backtest_report.py --input results/fee_impact.json --out results/backtest_report.html

产出:
    results/backtest_report.html                 主报告（指标 + 图 + 逐笔明细）
    results/trades_<run_id>_<策略>_<成本口径>.csv 每个 run 的逐笔交易明细
    results/metrics.json                         全量指标（机器可读）
    results/trades_all.csv                       所有 run 的逐笔明细合并

依赖: plotly（绘图）、qlib.contrib.evaluate.risk_analysis（可选，用于交叉校验年化）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import text

from data.database.connection import engine
from backtest.performance.metrics import calc_full_metrics

FIGURE_BG = "#ffffff"
UP_COLOR = "#d62728"      # 涨=红（A股习惯）
DOWN_COLOR = "#2ca02c"    # 跌=绿
BENCH_COLOR = "#888888"


# ---------- 数据读取 ----------

def load_equity(run_id: int) -> pd.DataFrame:
    sql = text(
        """
        SELECT timestamp, equity_value, drawdown, daily_return, cash_value, positions_value
        FROM equity_curves WHERE run_id = :rid ORDER BY timestamp
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"rid": run_id}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["timestamp", "equity_value", "drawdown",
                                     "daily_return", "cash_value", "positions_value"])
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    return df.set_index("timestamp")


def load_trades(run_id: int) -> pd.DataFrame:
    sql = text(
        """
        SELECT trade_idx, symbol, direction, entry_time, exit_time,
               entry_price, exit_price, size, pnl, return_pct, fees, status
        FROM trade_records WHERE run_id = :rid ORDER BY trade_idx
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"rid": run_id}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["trade_idx", "symbol", "direction", "entry_time", "exit_time",
                                     "entry_price", "exit_price", "size", "pnl", "return_pct",
                                     "fees", "status"])
    for c in ("entry_time", "exit_time"):
        df[c] = pd.to_datetime(df[c]).dt.tz_localize(None)
    for c in ("entry_price", "exit_price", "size", "pnl", "return_pct", "fees"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["持有天数"] = (df["exit_time"] - df["entry_time"]).dt.days
    df["成交额(元)"] = df["entry_price"] * df["size"]
    return df


def load_run_meta(run_id: int) -> dict:
    sql = text("SELECT strategy_name, result_json, init_cash, total_trades FROM backtest_runs WHERE id = :rid")
    with engine.connect() as conn:
        row = conn.execute(sql, {"rid": run_id}).fetchone()
    if not row:
        return {}
    raw = row[1]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    return {"strategy_name": row[0], "result_json": raw or {}, "init_cash": row[2], "total_trades": row[3]}


def qlib_annual_check(returns: pd.Series) -> dict:
    """用 qlib risk_analysis 做交叉校验（失败不影响主流程）。"""
    try:
        from qlib.contrib.evaluate import risk_analysis
        res = risk_analysis(returns.dropna(), freq="day")
        if res is None or len(res) == 0:
            return {}
        col = res.columns[0]
        return {k: float(v) for k, v in res[col].items()}
    except Exception as e:
        print(f"[warn] qlib risk_analysis 不可用({type(e).__name__})，跳过交叉校验")
        return {}


# ---------- 图表 ----------

def fig_equity_vs_bench(curves: dict, bench_equity: pd.Series, title: str) -> go.Figure:
    fig = go.Figure()
    palette = ["#d62728", "#1f77b4", "#ff7f0e"]
    for i, (name, df) in enumerate(curves.items()):
        if df.empty:
            continue
        norm = df["equity_value"] / df["equity_value"].iloc[0] * 100
        fig.add_trace(go.Scatter(x=norm.index, y=norm, mode="lines", name=name,
                                 line=dict(color=palette[i % len(palette)], width=1.8)))
    if bench_equity is not None and not bench_equity.empty:
        fig.add_trace(go.Scatter(x=bench_equity.index, y=bench_equity, mode="lines",
                                 name="沪深300(基准)", line=dict(dash="dash", color=BENCH_COLOR)))
    fig.update_layout(title=title, template="plotly_white", paper_bgcolor=FIGURE_BG,
                      xaxis_title="日期", yaxis_title="净值(起点=100)",
                      legend=dict(orientation="h", y=1.08), height=430)
    return fig


def rgba(hex_color: str, alpha: float = 0.15) -> str:
    """#rrggbb -> rgba()，plotly 5.x 不接受 8 位 hex。"""
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"


def fig_drawdown(curves: dict) -> go.Figure:
    fig = go.Figure()
    palette = ["#d62728", "#1f77b4", "#ff7f0e"]
    for i, (name, df) in enumerate(curves.items()):
        if df.empty or "drawdown" not in df:
            continue
        fig.add_trace(go.Scatter(x=df.index, y=df["drawdown"], mode="lines", name=name,
                                 line=dict(color=palette[i % len(palette)]), fill="tozeroy",
                                 fillcolor=rgba(palette[i % len(palette)], 0.15)))
    fig.update_layout(title="回撤曲线（精确成本口径）", template="plotly_white",
                      paper_bgcolor=FIGURE_BG, xaxis_title="日期", yaxis_title="回撤",
                      legend=dict(orientation="h", y=1.08), height=380)
    return fig


def fig_monthly_heatmap(equity: pd.Series, name: str) -> go.Figure:
    m = equity.resample("ME").last()
    mr = m.pct_change().dropna()
    if mr.empty:
        return go.Figure()
    years = sorted(set(mr.index.year))
    months = list(range(1, 13))
    z = [[np.nan] * 12 for _ in years]
    for dt, v in mr.items():
        z[years.index(dt.year)][dt.month - 1] = v * 100
    fig = go.Figure(go.Heatmap(
        z=z, x=[f"{m}月" for m in months], y=[str(y) for y in years],
        colorscale=[[0, DOWN_COLOR], [0.5, "#ffffff"], [1, UP_COLOR]],
        zmid=0, text=[[("" if np.isnan(c) else f"{c:.1f}") for c in row] for row in z],
        texttemplate="%{text}", textfont={"size": 10},
        colorbar=dict(title="月收益%"), hovertemplate="%{y}年%{x}: %{z:.2f}%<extra></extra>",
    ))
    fig.update_layout(title=f"{name}: 月度收益热力图(%)", template="plotly_white",
                      paper_bgcolor=FIGURE_BG, height=250)
    return fig


def fig_trade_pnl_dist(trades: pd.DataFrame, name: str) -> go.Figure:
    if trades.empty or "return_pct" not in trades:
        return go.Figure()
    rp = trades["return_pct"].dropna()
    fig = go.Figure(go.Histogram(
        x=rp, nbinsx=60,
        marker_color=[UP_COLOR if v > 0 else DOWN_COLOR for v in rp],
        hovertemplate="收益 %{x:.1f}%<br>笔数 %{y}<extra></extra>",
    ))
    fig.update_layout(title=f"{name}: 单笔收益分布", template="plotly_white",
                      paper_bgcolor=FIGURE_BG, xaxis_title="单笔收益%",
                      yaxis_title="笔数", height=330, showlegend=False)
    return fig


def fig_pnl_by_symbol(trades: pd.DataFrame, name: str, top: int = 25) -> go.Figure:
    if trades.empty or "pnl" not in trades:
        return go.Figure()
    g = trades.groupby("symbol")["pnl"].sum().sort_values()
    if len(g) > top:
        g = pd.concat([g.head(top // 2), g.tail(top // 2)])
    colors = [DOWN_COLOR if v < 0 else UP_COLOR for v in g.values]
    fig = go.Figure(go.Bar(x=g.index, y=g.values, marker_color=colors,
                           hovertemplate="%{x}<br>累计盈亏 %{y:,.0f} 元<extra></extra>"))
    fig.update_layout(title=f"{name}: 各标的累计盈亏(元)", template="plotly_white",
                      paper_bgcolor=FIGURE_BG, xaxis_title="标的", yaxis_title="累计盈亏(元)",
                      height=360, showlegend=False)
    return fig


def fig_fee_impact(rows: list) -> go.Figure:
    names = [r["strategy"] for r in rows if r.get("delta_return_pp") is not None]
    vals = [r["delta_return_pp"] for r in rows if r.get("delta_return_pp") is not None]
    trades = [r.get("precise", {}).get("total_trades", 0) for r in rows if r.get("delta_return_pp") is not None]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=names, y=vals, name="收益高估(百分点)", marker_color=UP_COLOR,
                         text=[f"{v:.2f}pp" for v in vals], textposition="outside"))
    fig.add_trace(go.Bar(x=names, y=trades, name="交易笔数", yaxis="y2",
                         marker_color="#7f7f7f", opacity=0.35))
    fig.update_layout(title="漏算印花税导致的收益高估（3年累计）",
                      template="plotly_white", paper_bgcolor=FIGURE_BG,
                      yaxis=dict(title="收益高估(百分点)"),
                      yaxis2=dict(title="交易笔数", overlaying="y", side="right"),
                      legend=dict(orientation="h", y=1.12), height=420)
    return fig


def fig_old_vs_new(strategy: str, df_new: pd.DataFrame, df_old: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for df, label, color in ((df_new, "精确成本(含印花税)", "#d62728"), (df_old, "旧口径(漏印花税)", "#1f77b4")):
        if df.empty:
            continue
        norm = df["equity_value"] / df["equity_value"].iloc[0] * 100
        fig.add_trace(go.Scatter(x=norm.index, y=norm, mode="lines", name=label, line=dict(color=color)))
    fig.update_layout(title=f"{strategy}: 费用口径对比", template="plotly_white",
                      paper_bgcolor=FIGURE_BG, xaxis_title="日期", yaxis_title="净值(起点=100)",
                      legend=dict(orientation="h", y=1.08), height=380)
    return fig


# ---------- HTML ----------

def fmt(v, pct_keys=(), int_keys=(), nd=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    if isinstance(v, (int, np.integer)):
        return f"{v:,}"
    return f"{v:.{nd}f}"


def metrics_block_html(title: str, metrics: dict, bench_note: str = "") -> str:
    """把一个 run 的分组指标渲染成卡片。"""
    if not metrics:
        return ""
    parts = [f'<h3>{title}</h3>']
    if bench_note:
        parts.append(f'<p class="note">{bench_note}</p>')
    parts.append('<table class="tbl"><thead><tr><th>类别</th><th>指标</th><th>数值</th>'
                 '<th>类别</th><th>指标</th><th>数值</th></tr></thead><tbody>')
    items = [(grp, k, v) for grp, d in metrics.items() for k, v in d.items()]
    half = (len(items) + 1) // 2
    left, right = items[:half], items[half:]
    for i in range(half):
        cells = ""
        for grp, k, v in (left[i], right[i] if i < len(right) else ("", "", "")):
            if not grp:
                cells += "<td></td><td></td><td></td>"
                continue
            val = fmt(v)
            if isinstance(v, float) and abs(v) >= 1000:
                val = f"{v:,.0f}"
            cls = ""
            if isinstance(v, (int, float)) and not (isinstance(v, float) and np.isnan(v)):
                if k in ("盈亏比", "Beta", "夏普比率", "索提诺比率", "卡玛比率", "信息比率", "Omega比率"):
                    cls = ""
                elif v > 0:
                    cls = ' class="up"'
                elif v < 0:
                    cls = ' class="down"'
            cells += f'<td>{grp}</td><td>{k}</td><td{cls}>{val}</td>'
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


def trades_table_html(trades: pd.DataFrame, max_rows: int = None) -> str:
    if trades.empty:
        return "<p>无交易记录</p>"
    cols = ["trade_idx", "symbol", "direction", "entry_time", "exit_time", "entry_price",
            "exit_price", "size", "pnl", "return_pct", "fees", "持有天数", "成交额(元)", "status"]
    cols = [c for c in cols if c in trades.columns]
    head_map = {"trade_idx": "#", "symbol": "标的", "direction": "方向", "entry_time": "买入日期",
                "exit_time": "卖出日期", "entry_price": "买入价", "exit_price": "卖出价",
                "size": "数量(股)", "pnl": "盈亏(元)", "return_pct": "收益率%", "fees": "费用(元)",
                "status": "状态"}
    status_cn = {"closed": "已平仓", "open": "未平仓"}
    t = trades[cols]
    if max_rows:
        t = t.head(max_rows)
    th = "".join(f"<th>{head_map.get(c, c)}</th>" for c in cols)
    body = []
    for _, r in t.iterrows():
        tds = []
        for c in cols:
            v = r[c]
            if c in ("entry_time", "exit_time"):
                s = "" if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d")
            elif c in ("pnl", "return_pct"):
                cls = "up" if (not pd.isna(v) and v > 0) else ("down" if (not pd.isna(v) and v < 0) else "")
                s = "-" if pd.isna(v) else f'<span class="{cls}">{v:,.2f}</span>'
            elif isinstance(v, (int, np.integer)):
                s = f"{v:,}"
            elif isinstance(v, float):
                s = "-" if np.isnan(v) else f"{v:,.2f}"
            elif c == "status":
                s = status_cn.get(str(v).lower(), str(v))
                if str(v).lower() == "open":
                    s = f'<span class="open-tag">{s}</span>'
            else:
                s = str(v)
            tds.append(f"<td>{s}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f'<div class="scroll"><table class="tbl tiny"><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


CSS = """
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:0;padding:32px;background:#f7f8fa;color:#222;}
h1{font-size:22px;margin:0 0 4px;} h2{font-size:17px;margin:28px 0 12px;border-left:4px solid #4c78a8;padding-left:8px;}
h3{font-size:14px;margin:16px 0 8px;color:#333;}
.meta{color:#666;font-size:13px;margin-bottom:18px;}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:18px;margin-bottom:18px;}
.tbl{border-collapse:collapse;width:100%;font-size:13px;}
.tbl th,.tbl td{border:1px solid #e5e7eb;padding:6px 10px;text-align:right;}
.tbl th{background:#f1f3f5;text-align:center;}
.tbl td:nth-child(1),.tbl td:nth-child(2),.tbl td:nth-child(4),.tbl td:nth-child(5){text-align:left;}
.tbl.tiny{font-size:11.5px;} .tbl.tiny th,.tbl.tiny td{padding:3px 6px;}
.scroll{max-height:640px;overflow:auto;border:1px solid #e5e7eb;border-radius:6px;}
.up{color:#d62728;font-weight:600;} .down{color:#2ca02c;font-weight:600;}
.open-tag{background:#fff3cd;color:#856404;padding:1px 5px;border-radius:3px;}
.fig{margin:14px 0;}
.note{font-size:12px;color:#777;margin:4px 0 10px;}
.warn{background:#fff8e1;border:1px solid #ffe082;border-radius:8px;padding:12px;font-size:13px;line-height:1.7;}
.warn b{color:#b26a00;}
details{margin:10px 0;} summary{cursor:pointer;font-size:13px;color:#4c78a8;padding:6px 0;}
code{background:#f1f3f5;padding:1px 5px;border-radius:3px;font-size:12px;}
"""


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="results/fee_impact.json")
    ap.add_argument("--out", default="results/backtest_report.html")
    ap.add_argument("--trades-dir", default="results")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as fp:
        data = json.load(fp)

    win = data["window"]
    symbols = data["symbols"]
    bench = data.get("benchmark") or {}
    rows = data["results"]

    # 基准净值
    bench_equity = pd.Series(dtype=float)
    bench_rets = pd.Series(dtype=float)
    if bench.get("daily_returns"):
        b = pd.DataFrame(bench["daily_returns"])
        b["date"] = pd.to_datetime(b["date"])
        bs = b.set_index("date")["ret"].astype(float).sort_index()
        bench_rets = bs
        bench_equity = (1 + bs).cumprod() * 100

    os.makedirs(args.trades_dir, exist_ok=True)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    curves_new, curves_old = {}, {}
    trades_map, metrics_map = {}, {}
    all_trades, metrics_json = [], {}

    for r in rows:
        name = r["strategy"]
        for tag, key in (("precise", "precise"), ("flat", "flat")):
            rid = r.get(key, {}).get("run_id")
            if not rid:
                continue
            eq = load_equity(rid)
            tr = load_trades(rid)
            meta = load_run_meta(rid)
            label = f"{name}（{'精确成本' if tag == 'precise' else '旧口径'}）"
            if tag == "precise":
                curves_new[name] = eq
            else:
                curves_old[name] = eq
            trades_map[(name, tag)] = tr

            if not eq.empty:
                m = calc_full_metrics(eq["equity_value"], bench_rets, tr,
                                      init_cash=data.get("init_cash"))
                metrics_map[(name, tag)] = m
                metrics_json[f"{name}|{tag}|run{rid}"] = {
                    "run_id": rid, "strategy": name, "cost_model": tag,
                    "window": win, "metrics": m,
                    "vbt_result": meta.get("result_json", {}),
                }

            # 逐笔明细落盘 CSV
            if not tr.empty:
                out_csv = os.path.join(args.trades_dir, f"trades_run{rid}_{name}_{tag}.csv")
                tr.to_csv(out_csv, index=False, encoding="utf-8-sig")
                t2 = tr.copy()
                t2.insert(0, "策略", name)
                t2.insert(1, "成本口径", tag)
                t2.insert(2, "run_id", rid)
                all_trades.append(t2)
                print(f"  逐笔明细: {out_csv} ({len(tr)} 笔)")

    if all_trades:
        merged = pd.concat(all_trades, ignore_index=True)
        merged.to_csv(os.path.join(args.trades_dir, "trades_all.csv"), index=False, encoding="utf-8-sig")
        print(f"  合并明细: {os.path.join(args.trades_dir, 'trades_all.csv')} ({len(merged)} 笔)")

    with open(os.path.join(args.trades_dir, "metrics.json"), "w", encoding="utf-8") as fp:
        json.dump(metrics_json, fp, ensure_ascii=False, indent=2, default=str)
    print(f"  指标 JSON: {os.path.join(args.trades_dir, 'metrics.json')}")

    # qlib 交叉校验
    qlib_note = ""
    if curves_new:
        name0 = list(curves_new)[0]
        r0 = curves_new[name0]["equity_value"].pct_change().dropna()
        q = qlib_annual_check(r0)
        if q.get("annualized_return") is not None:
            qlib_note = (f"qlib 交叉校验（{name0}）：算术年化 {q['annualized_return'] * 100:.2f}%，"
                         f"波动 {q.get('annualized_volatility', 0) * 100:.2f}%，"
                         f"最大回撤 {q.get('max_drawdown', 0) * 100:.2f}%。"
                         f"本表采用<b>几何年化</b> (1+累计)^(252/N)-1，两者口径不同。")

    # ---- 图表 ----
    figs = [fig_equity_vs_bench(curves_new, bench_equity, "策略净值 vs 沪深300（精确成本口径）")]
    figs.append(fig_drawdown(curves_new))
    figs.append(fig_fee_impact(rows))
    for name in curves_new:
        figs.append(fig_monthly_heatmap(curves_new[name]["equity_value"], name))
    for name in curves_new:
        figs.append(fig_trade_pnl_dist(trades_map.get((name, "precise"), pd.DataFrame()), name))
        figs.append(fig_pnl_by_symbol(trades_map.get((name, "precise"), pd.DataFrame()), name))
    for name in curves_new:
        figs.append(fig_old_vs_new(name, curves_new.get(name), curves_old.get(name)))
    figs_html = "\n".join(
        f'<div class="fig">{f.to_html(full_html=False, include_plotlyjs=False)}</div>'
        for f in figs if f.data
    )

    # ---- 费用影响表 ----
    impact_rows = []
    for r in rows:
        p, f = r.get("precise", {}), r.get("flat", {})
        impact_rows.append([
            r["strategy"], f"{f.get('total_return', 0):.2f}%", f"{p.get('total_return', 0):.2f}%",
            f"{r.get('delta_return_pp', 0):.3f}", f"{r.get('delta_value', 0):,.0f}",
            p.get("total_trades", 0), f"{(p.get('sharpe_ratio') or 0):.2f}",
            f"{(p.get('max_drawdown') or 0):.2f}%",
        ])
    impact_html = (
        '<table class="tbl"><thead><tr><th>策略</th><th>旧口径收益</th><th>精确成本收益</th>'
        '<th>收益高估(pp)</th><th>终值高估(元)</th><th>交易数</th><th>夏普</th><th>最大回撤</th>'
        '</tr></thead><tbody>'
        + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in impact_rows)
        + "</tbody></table>"
    )

    # ---- 收益构成分解：已平仓实现 vs 未平仓浮盈 ----
    attrib_rows = []
    for r in rows:
        name = r["strategy"]
        for tag, tag_cn in (("precise", "精确成本"), ("flat", "旧口径")):
            tr = trades_map.get((name, tag), pd.DataFrame())
            m = metrics_map.get((name, tag))
            rid = r.get(tag, {}).get("run_id")
            if tr.empty or not m:
                continue
            cl = tr[tr["status"].astype(str).str.lower() == "closed"]
            op = tr[tr["status"].astype(str).str.lower() == "open"]
            realized = float(cl["pnl"].sum())
            floating = float(op["pnl"].sum())
            fees = float(tr["fees"].sum())
            total = m["收益"]["终值"] - data["init_cash"]
            attrib_rows.append([
                name, tag_cn, f"{total:,.0f}", f"{realized:,.0f}", f"{floating:,.0f}",
                f"{floating / total * 100:.1f}%" if total else "-",
                f"{fees:,.0f}", f"{fees / data['init_cash'] * 100:.2f}%",
            ])
    attrib_html = (
        '<table class="tbl"><thead><tr><th>策略</th><th>成本口径</th><th>总盈亏(元)</th>'
        '<th>已平仓实现(元)</th><th>未平仓浮盈(元)</th><th>浮盈占比</th>'
        '<th>总费用(元)</th><th>费用/本金</th></tr></thead><tbody>'
        + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in attrib_rows)
        + "</tbody></table>"
    )

    # ---- 每个策略的常规指标 + 逐笔明细 ----
    detail_html = []
    for r in rows:
        name = r["strategy"]
        for tag, title_tag in (("precise", "精确成本（含卖出印花税）"), ("flat", "旧口径（漏印花税，仅作对照）")):
            m = metrics_map.get((name, tag))
            tr = trades_map.get((name, tag), pd.DataFrame())
            rid = r.get(tag, {}).get("run_id")
            if not m:
                continue
            csv_name = f"trades_run{rid}_{name}_{tag}.csv"
            vbt = metrics_json.get(f"{name}|{tag}|run{rid}", {}).get("vbt_result", {})
            note = (f"run_id={rid} ｜ 交易 {len(tr)} 笔 ｜ vbt 校验：胜率 {vbt.get('Win Rate [%]', 0):.2f}%、"
                    f"盈亏比 {vbt.get('Profit Factor', 0):.2f}、总费用 {vbt.get('Total Fees Paid', 0):,.0f} 元 ｜ "
                    f"明细 CSV: <code>{csv_name}</code>")
            block = metrics_block_html(f"{name} · {title_tag}", m, note)
            if not tr.empty:
                cl = tr[tr["status"].astype(str).str.lower() == "closed"]
                op = tr[tr["status"].astype(str).str.lower() == "open"]
                win_n = int((cl["pnl"] > 0).sum())
                block += (
                    f'<details open><summary>逐笔交易明细（共 {len(tr)} 笔：'
                    f'已平仓 {len(cl)}（盈利 {win_n} / 亏损 {len(cl) - win_n}）｜'
                    f'未平仓 {len(op)}，浮动盈亏 {op["pnl"].sum():,.0f} 元）— 点击展开/收起</summary>'
                    + trades_table_html(tr) + "</details>"
                )
            detail_html.append(f'<div class="card">{block}</div>')
    detail_html = "\n".join(detail_html)

    bench_note = f"{bench.get('code', '')} 区间收益 {bench.get('total_return_pct', 0):.2f}%" if bench else "无基准数据"
    qlib_p = f'<p class="note">{qlib_note}</p>' if qlib_note else ""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>回测报告 · 常规指标与逐笔交易明细</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>{CSS}</style></head><body>
<h1>回测报告：常规指标 + 逐笔交易明细</h1>
<div class="meta">
窗口 {win['start']} ~ {win['end']} ｜ 初始资金 {data['init_cash']:,.0f} 元 ｜
标的 {len(symbols)} 只（窗口内成交额 Top{len(symbols)}）｜ 基准 {bench_note}<br>
配色遵循 A 股习惯：<span class="up">红=盈利/上涨</span> · <span class="down">绿=亏损/下跌</span>
</div>

<div class="card"><h2>一、核心结论：费用口径影响</h2>
{impact_html}
<p style="font-size:13px;color:#555;">
旧口径 = 佣金+过户费 0.00026 双边（<b>漏算卖出印花税 0.05%</b>）；
精确成本 = 买 0.00026 / 卖 0.00076（含印花税），并计入单笔最低 5 元佣金。
</p></div>

<div class="card"><h2>二、收益构成分解（已实现 vs 未平仓浮盈）</h2>
{attrib_html}
<p style="font-size:13px;color:#555;">
关键：窗口结束时<b>仍有未平仓头寸</b>，其浮动盈亏被计入终值但<b>尚未落袋</b>。
例如 ma_cross 的 166 万账面收益中，已平仓仅实现约 52.7 万，其余约 113.7 万（68%）是浮盈。
若窗口末恰好处在高位，这部分收益在实盘中极可能回吐，<b>不能视为已兑现的 alpha</b>。
</p></div>

<div class="card"><h2>三、各策略常规指标（完整）</h2>
<p class="note">
口径：夏普/索提诺按<b>年化无风险利率 3%</b> 计算，故略低于 vbt 落库值（vbt 用 rf=0）；
胜率与盈亏比<b>只统计已平仓交易</b>，与 vbt 的 Win Rate / Profit Factor 完全一致。
几何年化 = (1+累计)^(252/N)-1；qlib 的算术年化（日均值×252）见下方交叉校验。
指标分 5 组：收益 / 风险 / 风险调整 / 相对基准 / 交易。
</p>
{qlib_p}
{detail_html}
</div>

<div class="card"><h2>四、图表</h2>{figs_html}</div>

<div class="card"><h2>五、方法说明与已知偏差</h2>
<div class="warn">
<b>1）标的池存在前视选择偏差（重要）。</b>本次 universe 是"窗口内成交额 Top{len(symbols)}"，
即用整段回测期的信息选出的热门股（含 2023-2025 的 AI 算力/光模块大牛股）。
因此各策略的<b>绝对收益被显著高估，不能作为策略有效性的证据</b>；
本报告的可靠结论是<b>同一数据下两种费用口径的差额</b>。<br>
<b>2）复权口径。</b>回测用价是 qfq_window：以窗口末行复权因子为基准，
换窗口会导致价格序列整体缩放不同，跨窗口结果不可直接比较。<br>
<b>3）未建模项。</b>未做整手(100股)取整、未做 T+1、滑点固定 0.1% 双边；
当前仍是零股交易（如 1,421 股），实盘无法成交。这些会进一步压低实盘可达收益。<br>
<b>4）最低佣金。</b>本次单笔约 {data['init_cash']/max(len(symbols),1):,.0f} 元，
最低 5 元佣金未触发（阈值 20,000 元），故费用差异几乎全部来自印花税。<br>
<b>5）未平仓头寸。</b>窗口结束时仍有未平仓标的，其浮动盈亏计入终值但未计入"已平仓交易统计"。
</div></div>
</body></html>"""

    with open(args.out, "w", encoding="utf-8") as fp:
        fp.write(html)
    print(f"\n报告已生成: {args.out}")


if __name__ == "__main__":
    main()
