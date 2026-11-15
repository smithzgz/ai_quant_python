# -*- coding: utf-8 -*-
"""生成样本外(OOS)回测对比报告。

对比四类口径(均为 A 股分边精确成本):
  - IS-top20     : 原口径, 窗口内成交额 Top20 (含前视偏差)
  - OOS-liq_pt20 : 起点前流动性 Top20 (与 IS 同选股标准, 无前视) -> 隔离纯前视偏差
  - OOS-hs300    : 沪深300成分(2022-12-30 point-in-time, 300只, 无前视)
  - 沪深300       : 指数基准

产出:
  results/oos_report.html      对比报告(对照表 + 权益曲线 + 结论)
  results/oos_metrics.json     机器可读对比指标
  results/oos_trades_<run>.csv 各 OOS run 的逐笔交易明细
"""
import argparse
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from sqlalchemy import text

from data.database.connection import engine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UP_COLOR = "#d62728"
DOWN_COLOR = "#2ca02c"
BENCH_COLOR = "#444444"
COLORS = {"IS-top20": "#d62728", "OOS-liq_pt20": "#1f77b4",
          "OOS-hs300": "#ff7f0e", "沪深300": BENCH_COLOR}


# ---------- 读取 ----------

def load_equity(run_id: int) -> pd.Series:
    sql = text("SELECT timestamp, equity_value FROM equity_curves "
               "WHERE run_id = :rid ORDER BY timestamp")
    with engine.connect() as conn:
        rows = conn.execute(sql, {"rid": run_id}).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(r[0]).tz_localize(None): float(r[1]) for r in rows})
    return s


def load_trades(run_id: int) -> pd.DataFrame:
    sql = text(
        "SELECT trade_idx, symbol, direction, entry_time, exit_time, entry_price, "
        "exit_price, size, pnl, return_pct, fees, status FROM trade_records "
        "WHERE run_id = :rid ORDER BY trade_idx"
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"rid": run_id}).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["trade_idx", "symbol", "direction", "entry_time",
                                    "exit_time", "entry_price", "exit_price", "size",
                                    "pnl", "return_pct", "fees", "status"])
    for c in ("entry_time", "exit_time"):
        df[c] = pd.to_datetime(df[c]).dt.tz_localize(None)
    for c in ("entry_price", "exit_price", "size", "pnl", "return_pct", "fees"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["持有天数"] = (df["exit_time"] - df["entry_time"]).dt.days
    return df


def bench_equity(daily_returns: list) -> pd.Series:
    if not daily_returns:
        return pd.Series(dtype=float)
    idx = [pd.Timestamp(r["date"]) for r in daily_returns]
    vals = [1.0]
    for r in daily_returns:
        vals.append(vals[-1] * (1 + r["ret"]))
    return pd.Series(vals[1:], index=idx)


# ---------- 绘图 ----------

def fig_bar(comp: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for col in comp.columns:
        fig.add_trace(go.Bar(
            x=comp.index, y=comp[col].values,
            name=col, marker_color=COLORS.get(col, "#888"),
        ))
    fig.update_layout(
        title="总收益率对比：前视口径(IS) vs 样本外(OOS) vs 基准",
        barmode="group", template="plotly_white",
        yaxis_title="总收益率 (%)", xaxis_title="策略",
        legend=dict(orientation="h", y=-0.18),
    )
    return fig


def fig_equity(curves: dict, bench: pd.Series, strategies: list) -> go.Figure:
    fig = go.Figure()
    from plotly.subplots import make_subplots
    fig = make_subplots(rows=len(strategies), cols=1,
                        shared_xaxes=False, vertical_spacing=0.08,
                        subplot_titles=[f"{s} 归一化净值" for s in strategies])
    for i, s in enumerate(strategies, start=1):
        for label, ser in curves[s].items():
            if ser is None or ser.empty:
                continue
            norm = ser / ser.iloc[0]
            fig.add_trace(go.Scatter(x=norm.index, y=norm.values, mode="lines",
                                    name=label, line=dict(color=COLORS.get(label, "#888"),
                                                          width=2 if label == "沪深300" else 1.5),
                                    showlegend=(i == 1)),
                          row=i, col=1)
        if not bench.empty:
            bnorm = bench / bench.iloc[0]
            fig.add_trace(go.Scatter(x=bnorm.index, y=bnorm.values, mode="lines",
                                    name="沪深300", line=dict(color=BENCH_COLOR, dash="dash"),
                                    showlegend=(i == 1)), row=i, col=1)
    fig.update_layout(template="plotly_white", height=240 * len(strategies),
                      legend=dict(orientation="h", y=-0.05),
                      title="归一化权益曲线（起点=1.0）")
    return fig


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="results/oos_backtest.json")
    ap.add_argument("--out", default="results/oos_report.html")
    args = ap.parse_args()

    d = json.load(open(args.input, encoding="utf-8"))
    is_p = d["is_top20_precise"]
    oos = d["oos"]
    bench = d["benchmark"]
    strategies = list(is_p.keys())

    # 对照表
    rows = []
    for s in strategies:
        isr = is_p.get(s, {})
        liq = oos["liq_pt20"]["strategies"].get(s, {})
        hs = oos["hs300"]["strategies"].get(s, {})
        rows.append({
            "策略": s,
            "IS-top20(前视)收益%": round(isr.get("total_return") or 0, 2),
            "IS夏普": round(isr.get("sharpe_ratio") or 0, 2),
            "OOS-流动性Top20收益%": round(liq.get("total_return") or 0, 2),
            "OOS流动性夏普": round(liq.get("sharpe_ratio") or 0, 2),
            "OOS-沪深300收益%": round(hs.get("total_return") or 0, 2),
            "OOS沪深300夏普": round(hs.get("sharpe_ratio") or 0, 2),
            "前视虚增(pp,vs流动性)": round((isr.get("total_return") or 0) - (liq.get("total_return") or 0), 2),
            "前视虚增(pp,vs300)": round((isr.get("total_return") or 0) - (hs.get("total_return") or 0), 2),
            "基准300%": round(bench.get("total_return_pct") or 0, 2),
        })
    comp = pd.DataFrame(rows).set_index("策略")

    # 权益曲线
    bench_ser = bench_equity(bench.get("daily_returns", []))
    curves = {s: {} for s in strategies}
    for s in strategies:
        curves[s]["IS-top20"] = load_equity(is_p[s].get("run_id")) if is_p.get(s, {}).get("run_id") else None
        curves[s]["OOS-liq_pt20"] = load_equity(oos["liq_pt20"]["strategies"][s].get("run_id")) if oos["liq_pt20"]["strategies"].get(s, {}).get("run_id") else None
        curves[s]["OOS-hs300"] = load_equity(oos["hs300"]["strategies"][s].get("run_id")) if oos["hs300"]["strategies"].get(s, {}).get("run_id") else None

    fig1 = fig_bar(comp[[c for c in comp.columns if "收益%" in c and ("IS" in c or "OOS" in c or "基准" in c)]])
    fig2 = fig_equity(curves, bench_ser, strategies)

    # 逐笔明细导出
    trade_csvs = []
    for key in ("liq_pt20", "hs300"):
        for s in strategies:
            v = oos[key]["strategies"].get(s, {})
            rid = v.get("run_id")
            if rid:
                tdf = load_trades(rid)
                if not tdf.empty:
                    fn = f"results/oos_trades_{rid}_{s}_{key}.csv"
                    tdf.to_csv(fn, index=False, encoding="utf-8-sig")
                    trade_csvs.append(fn)

    # 机器可读指标
    metrics = {
        "window": d["window"], "init_cash": d["init_cash"],
        "benchmark_300_pct": bench.get("total_return_pct"),
        "comparison": comp.reset_index().to_dict(orient="records"),
        "note": d["note"],
    }
    with open("results/oos_metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # 结论文案
    is_tr = {s: is_p[s].get("total_return") for s in strategies}
    liq_tr = {s: oos["liq_pt20"]["strategies"][s].get("total_return") for s in strategies}
    hs_tr = {s: oos["hs300"]["strategies"][s].get("total_return") for s in strategies}
    bench_tr = bench.get("total_return_pct")
    concl = []
    for s in strategies:
        concl.append(
            f"<li><b>{s}</b>：前视口径 {is_tr[s]:.1f}% → 起点前流动性Top20 {liq_tr[s]:.1f}% "
            f"→ 沪深300成分 {hs_tr[s]:.1f}%。前视偏差虚增约 "
            f"{(is_tr[s]-liq_tr[s]):.0f} 个百分点（相对 沪深300 {bench_tr:.1f}%）。</li>"
        )
    beat = sum(1 for s in strategies if liq_tr[s] > bench_tr)
    beat2 = sum(1 for s in strategies if hs_tr[s] > bench_tr)

    html = f"""
    <html><head><meta charset="utf-8">
    <style>body{{font-family:-apple-system,Segoe UI,Arial,'Microsoft YaHei',sans-serif;margin:24px;color:#222}}
    h1{{color:#1a3c6e}} h2{{color:#1a3c6e;border-bottom:2px solid #1a3c6e;padding-bottom:4px}}
    table{{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px}}
    th,td{{border:1px solid #ccc;padding:6px 8px;text-align:right}}
    th{{background:#1a3c6e;color:#fff}} td:first-child,th:first-child{{text-align:left}}
    .warn{{background:#fff4e6;border-left:4px solid #ff7f0e;padding:10px 14px;margin:14px 0}}
    .good{{background:#eafaf0;border-left:4px solid #2ca02c;padding:10px 14px;margin:14px 0}}
    .big{{font-size:15px}}</style></head><body>
    <h1>样本外(OOS)回测报告：消除标的池前视选择偏差</h1>
    <p class="big">窗口 {d['window']['start']} ~ {d['window']['end']}　初始资金 {int(d['init_cash']):,} 元　成本模型：A股分边精确成本</p>

    <h2>一、对照表（总收益率 %）</h2>
    {comp.to_html(classes='', border=0)}
    <p>注：IS-top20 = 原口径（窗口内成交额 Top20，含前视偏差）；OOS-流动性Top20 = 起点前200日流动性 Top20（与 IS 同选股标准、无前视）；
    OOS-沪深300 = 2022-12-30 成分（300只，无前视）；基准 = 沪深300 指数。</p>

    <h2>二、总收益率柱状对比</h2>
    {fig1.to_html(full_html=False, include_plotlyjs='cdn')}

    <h2>三、归一化权益曲线</h2>
    {fig2.to_html(full_html=False, include_plotlyjs='cdn')}

    <h2>四、核心结论</h2>
    <div class="warn"><b>前视偏差是 IS 结果的主要"收益来源"。</b>
    <ul>{''.join(concl)}</ul>
    把"窗口内成交额 Top20"换成"起点即可知的成分/流动性"（选股标准不变），三个简单策略的收益从
    {is_tr['ma_cross']:.0f}/{is_tr['macd']:.0f}/{is_tr['momentum']:.0f}% 骤降到
    {liq_tr['ma_cross']:.1f}/{liq_tr['macd']:.1f}/{liq_tr['momentum']:.1f}%（流动性Top20口径）。
    这说明原 IS 收益绝大部分来自"用未来信息选出了后来最活跃的赢家股"，而非策略信号本身。</div>

    <div class="{'good' if beat>=1 else 'warn'}"><b>样本外后能否跑赢沪深300（{bench_tr:.2f}%）？</b>
    在"起点前流动性Top20"口径下，仅 {beat}/3 个策略小幅跑赢基准；在"沪深300成分"口径下仅 {beat2}/3 跑赢。
    即去除前视偏差后，这三个简单择时/动量与沪深300相比并无稳定超额，部分甚至跑输。</div>

    <h2>五、重要说明（避免误读）</h2>
    <ul>
      <li><b>沪深300成分口径资本过散：</b>300 只标的对 100 万本金，等权下每只仅约 3,300 元，高价股（如茅台）自然无法买入整手（size=0），组合实际仅交易低价成分股且大量持币。该口径主要说明"大盘成分+简单信号"的弱表现，绝对值不宜直接对比流动性Top20。</li>
      <li><b>流动性Top20口径是更干净的对照：</b>它与 IS 选股标准完全一致，仅把"窗口内总额"改为"起点前数据"，因此两者之差可近似视为纯前视偏差贡献。</li>
      <li><b>仍存在的已知偏差：</b>复权价金额口径、约22%订单被资金缩减、约7.5%涨跌停未处理（与整改前一致，未在本次变更）。</li>
      <li><b>本回测仍非严格样本外验证：</b>参数（均线窗口等）未做参数搜寻隔离，且 universe 固定为起点日未做年度再平衡。若要严谨证明策略有效性，还需做滚动窗口/walk-forward 与参数稳定性检验。</li>
    </ul>

    <h2>六、附：OOS 逐笔明细已导出</h2>
    <p>{'<br>'.join(trade_csvs)}</p>
    <p style="color:#888;margin-top:24px">生成时间：{pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}</p>
    </body></html>
    """
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告已写入: {args.out}")
    print(f"逐笔明细: {len(trade_csvs)} 个文件")
    print(comp.to_string())


if __name__ == "__main__":
    main()
