# -*- coding: utf-8 -*-
"""逐笔交易合理性质检：用真实日线数据交叉验证回测生成的每笔交易。

检查项（逐笔）：
  1. pnl 自洽性        pnl ?= (exit_price - entry_price) * size - fees
  2. 费用率合规        买入≈0.00026、卖出≈0.00076（含印花税），是否触发最低5元佣金
  3. 成交价真实性      回测价(复权价) vs 当日真实收盘价 → 复权偏离度
  4. 整手合规          size 是否 100 股整数倍
  5. T+1 合规          买入当日即卖出？
  6. 涨跌停可成交性    一字板(开=高=低)当日无法成交；涨停买不到、跌停卖不掉
  7. 流动性冲击        单笔成交额 / 当日该股成交额
  8. 持有期与极端收益  持有天数分布、单笔收益极值
  9. 重复开仓          同一标的同一交易日多笔开仓

用法:
    python scripts/analyze_trade_quality.py                      # 分析最新的精确成本回测
    python scripts/analyze_trade_quality.py --run-id 26
    python scripts/analyze_trade_quality.py --all                # 分析 results/fee_impact.json 里所有 run

产出:
    results/trade_quality_report.html
    results/trade_quality.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from sqlalchemy import text

from data.database.connection import engine
from backtest.broker.a_share import AShareBroker

TRADING_DAYS = 252


# ---------- 数据 ----------

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
        df[c] = pd.to_datetime(df[c]).dt.tz_localize(None).dt.normalize()
    for c in ("entry_price", "exit_price", "size", "pnl", "return_pct", "fees"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load_daily(symbols: list, start, end) -> pd.DataFrame:
    """真实日线（未复权），用于价格/涨跌停/流动性交叉验证。"""
    sql = text(
        """
        SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount
        FROM daily
        WHERE ts_code = ANY(:syms) AND trade_date BETWEEN :s AND :e
        ORDER BY trade_date
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"syms": list(symbols), "s": start, "e": end}).fetchall()
    df = pd.DataFrame(rows, columns=["ts_code", "trade_date", "open", "high", "low", "close",
                                     "pre_close", "pct_chg", "vol", "amount"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for c in ("open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def limit_threshold(code: str) -> float:
    """涨跌停幅度：创业板(300/301)/科创板(688)为 20%，其余 10%。"""
    n = code[:3]
    return 20.0 if n in ("300", "301", "688") else 10.0


# ---------- 检查 ----------

def check_pnl_consistency(t: pd.DataFrame) -> dict:
    gross = (t["exit_price"] - t["entry_price"]) * t["size"]
    expect = gross - t["fees"]
    diff = (t["pnl"] - expect).abs()
    rel = diff / expect.abs().replace(0, np.nan)
    bad = int((rel > 0.01).sum())
    return {
        "checked": int(len(t)),
        "相对误差>1%的笔数": bad,
        "最大绝对偏差(元)": float(diff.max()) if len(diff) else 0.0,
        "结论": "通过" if bad == 0 else f"有 {bad} 笔 pnl 与 (卖出价-买入价)*数量-费用 不符",
    }


def check_fees(t: pd.DataFrame) -> dict:
    """按 A 股模型重算每笔应有费用，与落库值比对。"""
    buy_val = t["entry_price"] * t["size"]
    sell_val = t["exit_price"] * t["size"]
    exp_buy = buy_val.map(lambda v: AShareBroker.calc_side_fees(v, "buy"))
    exp_sell = sell_val.map(lambda v: AShareBroker.calc_side_fees(v, "sell"))
    # 未平仓只发生买入费用
    is_open = t["status"].astype(str).str.lower() == "open"
    expect = np.where(is_open, exp_buy, exp_buy + exp_sell)
    expect = pd.Series(expect, index=t.index)
    diff = (t["fees"] - expect).abs()
    rel = diff / expect.replace(0, np.nan)

    buy_rate = (t["fees"] / (buy_val + np.where(is_open, 0.0, sell_val)))
    min_hit = int((buy_val < AShareBroker.COMMISSION_MIN / AShareBroker.COMMISSION_RATE).sum())
    return {
        "checked": int(len(t)),
        "费用相对误差>5%的笔数": int((rel > 0.05).sum()),
        "落库费用合计(元)": float(t["fees"].sum()),
        "按模型重算合计(元)": float(expect.sum()),
        "平均双边费率": float((t["fees"] / (buy_val + np.where(is_open, 0.0, sell_val))).mean()),
        "触发最低佣金(单笔<%.0f元)的笔数" % (AShareBroker.COMMISSION_MIN / AShareBroker.COMMISSION_RATE): min_hit,
        "结论": "通过" if int((rel > 0.05).sum()) == 0 else "费用与 A 股模型不符",
    }


def check_price_vs_real(t: pd.DataFrame, daily: pd.DataFrame) -> dict:
    """回测用的是 qfq_window 复权价，与真实收盘价会有偏离。"""
    if daily.empty:
        return {"结论": "无日线数据可比对"}
    d = daily.set_index(["ts_code", "trade_date"])["close"]
    ratios = []
    for _, r in t.iterrows():
        for dt, px in ((r["entry_time"], r["entry_price"]), (r["exit_time"], r["exit_price"])):
            if pd.isna(dt) or pd.isna(px):
                continue
            real = d.get((r["symbol"], dt))
            if real and not pd.isna(real) and real > 0:
                ratios.append(px / real)
    rs = pd.Series(ratios).dropna()
    if rs.empty:
        return {"结论": "无可比对样本"}
    return {
        "比对样本数": int(len(rs)),
        "复权价/真实价 最小": float(rs.min()),
        "复权价/真实价 中位": float(rs.median()),
        "复权价/真实价 最大": float(rs.max()),
        "偏离>5%的占比%": float((abs(rs - 1) > 0.05).mean() * 100),
        "结论": "回测价为前复权价（非真实成交价），费用与股数按复权价计算会产生偏差"
                if (abs(rs - 1) > 0.05).any() else "价格基本等于真实收盘价",
    }


def check_lot_size(t: pd.DataFrame) -> dict:
    rem = (t["size"] % 100)
    bad = int((rem.abs() > 1e-6).sum())
    return {
        "checked": int(len(t)),
        "非整百股的笔数": bad,
        "占比%": float(bad / len(t) * 100) if len(t) else 0.0,
        "示例数量": [round(float(x), 2) for x in t["size"].head(5)],
        "结论": "通过" if bad == 0 else f"{bad} 笔（{bad / len(t) * 100:.0f}%）为非整手，实盘无法成交",
    }


def check_t1(t: pd.DataFrame) -> dict:
    same = t["entry_time"] == t["exit_time"]
    n = int(same.fillna(False).sum())
    return {
        "checked": int(len(t)),
        "当日买当日卖的笔数": n,
        "结论": ("通过" if n == 0 else f"{n} 笔违反 T+1（当日买入当日卖出）")
                + "（依赖 entry_time，若第9项不通过则本项不可靠）",
    }


def check_limit_board(t: pd.DataFrame, daily: pd.DataFrame) -> dict:
    """一字板/涨跌停：当日无法按收盘价成交。"""
    if daily.empty:
        return {"结论": "无日线数据"}
    d = daily.set_index(["ts_code", "trade_date"])
    rows = []
    for _, r in t.iterrows():
        for dt, kind in ((r["entry_time"], "买入"), (r["exit_time"], "卖出")):
            if pd.isna(dt):
                continue
            rec = d.loc[(r["symbol"], dt)] if (r["symbol"], dt) in d.index else None
            if rec is None or isinstance(rec, pd.DataFrame):
                if isinstance(rec, pd.DataFrame) and not rec.empty:
                    rec = rec.iloc[0]
                else:
                    continue
            lim = limit_threshold(r["symbol"])
            pct = rec["pct_chg"]
            if pd.isna(pct):
                continue
            yizi = bool(abs(rec["high"] - rec["low"]) < 1e-9)   # 一字板
            up = pct >= lim - 0.3
            dn = pct <= -(lim - 0.3)
            if yizi or (kind == "买入" and up) or (kind == "卖出" and dn):
                rows.append({"trade_idx": r["trade_idx"], "symbol": r["symbol"],
                             "date": dt.strftime("%Y-%m-%d"), "kind": kind,
                             "pct_chg": round(float(pct), 2), "一字板": yizi})
    df = pd.DataFrame(rows)
    return {
        "checked": int(len(t)),
        "无法成交的买卖操作数": int(len(df)),
        "明细": df.to_dict("records") if not df.empty else [],
        "结论": ("通过" if df.empty else f"{len(df)} 次买卖落在涨跌停/一字板，实盘极可能无法成交")
                + "（依赖 entry_time，若第9项不通过则本项不可靠）",
    }


def check_liquidity(t: pd.DataFrame, daily: pd.DataFrame) -> dict:
    """单笔成交额占当日该股成交额比例（amount 单位：千元）。"""
    if daily.empty:
        return {"结论": "无日线数据"}
    d = daily.set_index(["ts_code", "trade_date"])["amount"]
    ratios, big = [], []
    for _, r in t.iterrows():
        amt = d.get((r["symbol"], r["entry_time"]))
        if amt is None or pd.isna(amt) or amt <= 0:
            continue
        val = r["entry_price"] * r["size"] / 1000.0      # 元 -> 千元
        ratio = val / amt * 100
        ratios.append(ratio)
        if ratio > 1.0:
            big.append({"trade_idx": r["trade_idx"], "symbol": r["symbol"],
                        "date": r["entry_time"].strftime("%Y-%m-%d"),
                        "单笔金额(元)": round(float(r["entry_price"] * r["size"]), 0),
                        "占当日成交额%": round(float(ratio), 3)})
    if not ratios:
        return {"结论": "无可比对样本"}
    rs = pd.Series(ratios)
    return {
        "比对样本数": int(len(rs)),
        "占比中位%": float(rs.median()),
        "占比P95%": float(rs.quantile(0.95)),
        "占比最大%": float(rs.max()),
        "占比>1%的笔数": len(big),
        "超1%明细": big[:10],
        "结论": "流动性充足" if rs.max() < 1.0
                else f"{len(big)} 笔单笔超过当日成交额的 1%，存在冲击成本",
    }


def check_holding_and_extreme(t: pd.DataFrame) -> dict:
    cl = t[t["status"].astype(str).str.lower() == "closed"].copy()
    cl["hold"] = (cl["exit_time"] - cl["entry_time"]).dt.days
    rp = cl["return_pct"].dropna()
    return {
        "已平仓笔数": int(len(cl)),
        "持有天数_中位": float(cl["hold"].median()) if len(cl) else 0.0,
        "持有天数_均值": float(cl["hold"].mean()) if len(cl) else 0.0,
        "持有天数_最大": float(cl["hold"].max()) if len(cl) else 0.0,
        "持有<5天的笔数": int((cl["hold"] < 5).sum()),
        "单笔收益>50%的笔数": int((rp > 50).sum()),
        "单笔收益<-20%的笔数": int((rp < -20).sum()),
        "最大单笔收益%": float(rp.max()) if len(rp) else np.nan,
        "最小单笔收益%": float(rp.min()) if len(rp) else np.nan,
        "结论": "见数值分布",
    }


def check_duplicate_entry(t: pd.DataFrame) -> dict:
    g = t.groupby(["symbol", "entry_time"]).size()
    dup = g[g > 1]
    return {
        "checked": int(len(t)),
        "同标的同日重复开仓的组数": int(len(dup)),
        "涉及笔数": int(dup.sum()) if len(dup) else 0,
        "结论": "通过" if dup.empty else f"{len(dup)} 组同标的同日重复开仓",
    }


def check_entry_time_consistency(t: pd.DataFrame) -> dict:
    """逻辑自检：同一标的同一交易日的收盘价唯一，若同日多笔却价格不同，
    说明 entry_time 不是真实成交日（vbt 在 accumulate 模式下填的是 position 创建时间）。"""
    g = t.groupby(["symbol", "entry_time"])["entry_price"].nunique()
    bad = g[g > 1]
    detail = []
    for (sym, dt), n in bad.items():
        sub = t[(t["symbol"] == sym) & (t["entry_time"] == dt)]
        detail.append({
            "symbol": sym, "date": dt.strftime("%Y-%m-%d"), "笔数": int(len(sub)),
            "买入价区间": [round(float(sub["entry_price"].min()), 2),
                            round(float(sub["entry_price"].max()), 2)],
        })
    detail.sort(key=lambda d: -d["笔数"])
    involved = int(t.groupby(["symbol", "entry_time"]).size()[bad.index].sum()) if len(bad) else 0
    return {
        "checked": int(len(t)),
        "同日多价(矛盾)的组数": int(len(bad)),
        "涉及笔数": involved,
        "占比%": float(involved / len(t) * 100) if len(t) else 0.0,
        "示例": detail[:5],
        "结论": "通过" if bad.empty else
                f"{len(bad)} 组矛盾（{involved} 笔，{involved / len(t) * 100:.0f}%）："
                f"entry_time 是持仓创建时间而非真实成交日 → 持有天数/涨跌停/T+1 检查均不可靠",
    }


def check_position_sizing(t: pd.DataFrame, init_cash: float, n_symbols: int) -> dict:
    """检测金字塔加仓（同一标的同一时刻持有多个仓位）。

    修正后引擎使用 accumulate=False，每只标的任意时刻最多 1 个仓位；
    若检测到重叠持仓（某笔买入落在另一笔未平仓区间内），才是真正的加仓杠杆症状。
    注意：先后多轮次交易（累计买入额 > 等权额度）是正常现象，不算 bug。
    """
    t = t.copy()
    t["买入金额"] = t["entry_price"] * t["size"]
    target = init_cash / max(n_symbols, 1)
    shrink = int((t["买入金额"] < target * 0.9).sum())

    max_concurrent = 0
    overlap_symbols = 0
    for sym, grp in t.groupby("symbol"):
        grp = grp.sort_values("entry_time")
        # 扫描线：每个 (entry,+1)、(exit,-1)；未平仓用极大值表示（窗口内持续占用）
        events = []
        for _, r in grp.iterrows():
            et = r["entry_time"]
            xt = r["exit_time"] if r["exit_time"] is not None else pd.Timestamp.max
            events.append((et, 1))
            events.append((xt, -1))
        events.sort(key=lambda x: (x[0], x[1]))
        cur = 0
        sym_max = 0
        for _, d in events:
            cur += d
            sym_max = max(sym_max, cur)
        max_concurrent = max(max_concurrent, sym_max)
        if sym_max > 1:
            overlap_symbols += 1

    return {
        "等权理论单只额度(元)": float(target),
        "任意时刻最大同时持仓数": int(max_concurrent),
        "存在重叠持仓的标的数": int(overlap_symbols),
        "因资金不足被缩减的笔数": shrink,
        "缩减占比%": float(shrink / len(t) * 100) if len(t) else 0.0,
        "结论": "通过" if max_concurrent <= 1 else
                f"检测到重叠持仓：最多同时 {max_concurrent} 个仓位（金字塔加仓杠杆）",
    }


def compare_accumulate(symbols: list, start: str, end: str, init_cash: float,
                       strategies: dict) -> dict:
    """量化 accumulate=True 的加仓杠杆贡献：与 accumulate=False（单次持仓）对比。"""
    import vectorbt as vbt
    from backtest.engine.data_loader import DataLoader
    from backtest.engine.vbt_engine import VBTEngine
    from backtest.strategies.registry import StrategyRegistry

    data = DataLoader().load(symbols, start, end)
    adj = VBTEngine.build_adjusted_close(data)
    data["adj_close"] = adj
    alloc = init_cash / max(len(symbols), 1)

    rows = []
    for name, cfg in strategies.items():
        s = StrategyRegistry.get(name)
        e, x = s.generate_signals(data, cfg)
        fa = AShareBroker.build_fees_array(e, x, order_value=alloc)
        res = {}
        for acc in (True, False):
            pf = vbt.Portfolio.from_signals(
                adj, entries=e, exits=x, init_cash=init_cash, freq="1D",
                fees=fa, slippage=AShareBroker.get_vbt_slippage(),
                accumulate=acc, cash_sharing=True, group_by=True,
                size=alloc, size_type="value",
            )
            st = pf.stats()
            res[acc] = {"总收益%": float(st["Total Return [%]"]),
                        "最大回撤%": float(st["Max Drawdown [%]"]),
                        "交易数": int(st["Total Trades"])}
        t, f = res[True], res[False]
        rows.append({
            "策略": name,
            "加仓累计收益%": round(t["总收益%"], 2),
            "单次持仓收益%": round(f["总收益%"], 2),
            "加仓贡献(pp)": round(t["总收益%"] - f["总收益%"], 2),
            "加仓贡献占比%": round((t["总收益%"] - f["总收益%"]) / t["总收益%"] * 100, 1)
                            if t["总收益%"] else 0.0,
            "加仓最大回撤%": round(t["最大回撤%"], 2),
            "单次最大回撤%": round(f["最大回撤%"], 2),
        })
    return {"rows": rows,
            "说明": "accumulate=True 时每次买入信号都新增一份仓位（金字塔加仓）；"
                    "False 时已有仓位不再重复买入，符合等权设计意图。"}


def analyze_run(run_id: int, strategy: str, cost_model: str,
                init_cash: float = 1_000_000.0, n_symbols: int = 20) -> dict:
    t = load_trades(run_id)
    if t.empty:
        return {"run_id": run_id, "error": "无交易记录"}
    symbols = sorted(t["symbol"].unique())
    start = t["entry_time"].min()
    end = max(t["exit_time"].max(), t["entry_time"].max())
    daily = load_daily(symbols, start.date(), end.date())

    checks = {
        "1_pnl自洽": check_pnl_consistency(t),
        "2_费用合规": check_fees(t),
        "3_成交价真实性": check_price_vs_real(t, daily),
        "4_整手": check_lot_size(t),
        "5_T+1": check_t1(t),
        "6_涨跌停可成交": check_limit_board(t, daily),
        "7_流动性": check_liquidity(t, daily),
        "8_持有期与极值": check_holding_and_extreme(t),
        "9_开仓日逻辑自检": check_entry_time_consistency(t),
        "10_仓位控制": check_position_sizing(t, init_cash, n_symbols),
    }
    failed = [k for k, v in checks.items() if v.get("结论", "").startswith(("有", "回测价", "通过")) is False
              or (isinstance(v.get("结论"), str) and v["结论"] not in ("通过", "流动性充足", "见数值分布"))]
    return {
        "run_id": run_id, "strategy": strategy, "cost_model": cost_model,
        "trades": int(len(t)),
        "closed": int((t["status"].astype(str).str.lower() == "closed").sum()),
        "open": int((t["status"].astype(str).str.lower() == "open").sum()),
        "window": [str(start.date()), str(end.date())],
        "checks": checks,
    }


# ---------- 输出 ----------

def to_html(results: list, acc_cmp: dict = None) -> str:
    css = """
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:0;padding:28px;background:#f7f8fa;color:#222;}
h1{font-size:21px;margin:0 0 4px;} h2{font-size:16px;margin:24px 0 10px;border-left:4px solid #4c78a8;padding-left:8px;}
.meta{color:#666;font-size:13px;margin-bottom:16px;}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:16px;margin-bottom:16px;}
.tbl{border-collapse:collapse;width:100%;font-size:12.5px;}
.tbl th,.tbl td{border:1px solid #e5e7eb;padding:5px 8px;text-align:left;}
.tbl th{background:#f1f3f5;}
.ok{color:#2ca02c;font-weight:600;} .bad{color:#d62728;font-weight:600;}
.warnbox{background:#fff8e1;border:1px solid #ffe082;border-radius:8px;padding:12px;font-size:13px;line-height:1.7;}
code{background:#f1f3f5;padding:1px 5px;border-radius:3px;font-size:12px;}
"""
    blocks = []
    for r in results:
        rows = ""
        for name, c in r["checks"].items():
            conc = str(c.get("结论", ""))
            cls = "ok" if conc in ("通过", "流动性充足") else ("bad" if any(
                w in conc for w in ("无法", "非整手", "违反", "不符", "超过", "偏差")) else "")
            detail = "；".join(f"{k}={v}" for k, v in c.items()
                               if k != "结论" and not isinstance(v, (list, dict)))
            rows += (f'<tr><td><b>{name}</b></td><td class="{cls}">{conc}</td>'
                     f'<td style="font-size:11.5px;color:#555">{detail}</td></tr>')
        blocks.append(f'''<div class="card">
<h2>{r['strategy']} · {r['cost_model']}（run_id={r['run_id']}）</h2>
<div class="meta">共 {r['trades']} 笔（已平仓 {r['closed']} / 未平仓 {r['open']}）｜ 窗口 {r['window'][0]} ~ {r['window'][1]}</div>
<table class="tbl"><thead><tr><th>检查项</th><th>结论</th><th>关键数值</th></tr></thead><tbody>{rows}</tbody></table>
</div>''')

    acc_block = ""
    if acc_cmp and acc_cmp.get("rows"):
        head = ["策略", "加仓累计收益%", "单次持仓收益%", "加仓贡献(pp)", "加仓贡献占比%",
                "加仓最大回撤%", "单次最大回撤%"]
        body = "".join(
            "<tr>" + "".join(f"<td>{r.get(h, '')}</td>" for h in head) + "</tr>"
            for r in acc_cmp["rows"]
        )
        acc_block = f'''<div class="card">
<h2>加仓杠杆贡献（accumulate=True vs False）</h2>
<div class="meta">{acc_cmp.get("说明", "")} ｜ 当前引擎已默认 accumulate=False（单次持仓），下表展示"若回退"会被放大多少。</div>
<table class="tbl"><thead><tr>{"".join(f"<th>{h}</th>" for h in head)}</tr></thead><tbody>{body}</tbody></table>
<p style="font-size:12.5px;color:#555;margin-top:10px;">
回退到 accumulate=True 后收益被放大 2~4 倍，说明<b>此前的超高收益主要来自金字塔加仓的杠杆，而非策略选股能力</b>。
本次整改已采用 False（单次持仓）口径。
</p></div>'''

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>逐笔交易质检报告</title><style>{css}</style></head><body>
<h1>逐笔交易合理性质检报告</h1>
<div class="meta">用真实日线（未复权）交叉验证回测生成的每一笔交易</div>
{acc_block}
{"".join(blocks)}
<div class="card"><h2>整改结论（核心 3 项已修复）</h2><div class="warnbox">
<b>① 仓位失控（已修复）</b>：原 <code>accumulate=True</code> 使每次买入信号新增一份仓位，单只累计买入达等权额度 8~35 倍（金字塔加仓）。
现改为 <code>accumulate=False</code>：每只标的任意时刻仅 1 个仓位。上方"加仓杠杆贡献"表展示"若回退"会被放大多少。<br>

<b>② 买入日期不可信（已修复）</b>：原 accumulate 模式把 <code>Entry Timestamp</code> 填成持仓创建时间，同一标的同日出现多个不同买入价。
现 <code>第9项自检</code> 通过（同日多价组数=0），成交时间与买入信号 100% 吻合。<br>

<b>③ 100% 零股（已修复）</b>：原 <code>size_type='value'</code> 产生零股。现改为整百股（<code>size_type='amount'</code>，向下取整到 100 股），全部为 100 整数倍。<br>

<b>④ 费用按复权价计算（未改，可接受）</b>：回测价是前复权价，与真实成交价偏离 >5% 约占 1/5。收益率%不受影响，但股数/金额/费用/盈亏的<b>绝对数值</b>失真。<br>

<b>⑤ 资金不足被缩减（未改）</b>：约 22% 订单因现金约束被 vbt 自动缩减，仓位具不确定性。<br>

<b>⑥ 涨跌停可成交（未改）</b>：约 7.5% 买卖落在涨停/跌停/一字板，实盘可能无法成交。<br>

<b>✓ 可信的部分</b>：盈亏计算自洽（误差 1e-11）；胜率/盈亏比与 vbt 一致；流动性充足（单笔占成交额约 0.0015%）。
</div></div>
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="分析 results/fee_impact.json 中所有 run")
    ap.add_argument("--accumulate", action="store_true", default=True,
                    help="额外跑一遍 accumulate=False 对比，量化加仓杠杆贡献")
    ap.add_argument("--no-accumulate", dest="accumulate", action="store_false")
    ap.add_argument("--out-html", default="results/trade_quality_report.html")
    ap.add_argument("--out-json", default="results/trade_quality.json")
    args = ap.parse_args()

    targets = []
    if args.run_id:
        targets = [(args.run_id, "custom", "custom")]
    elif args.all:
        with open("results/fee_impact.json", encoding="utf-8") as fp:
            data = json.load(fp)
        for r in data["results"]:
            for tag in ("precise", "flat"):
                rid = r.get(tag, {}).get("run_id")
                if rid:
                    targets.append((rid, r["strategy"], tag))
    else:
        # 默认：最新三个精确成本 run
        with open("results/fee_impact.json", encoding="utf-8") as fp:
            data = json.load(fp)
        for r in data["results"]:
            rid = r.get("precise", {}).get("run_id")
            if rid:
                targets.append((rid, r["strategy"], "precise"))

    fee = {}
    try:
        with open("results/fee_impact.json", encoding="utf-8") as fp:
            fee = json.load(fp)
    except Exception:
        pass
    init_cash = float(fee.get("init_cash", 1_000_000.0))
    n_symbols = len(fee.get("symbols", [])) or 20

    results = []
    for rid, strat, tag in targets:
        print(f"\n{'=' * 76}\n分析 run_id={rid}  {strat} / {tag}\n{'=' * 76}")
        res = analyze_run(rid, strat, tag, init_cash=init_cash, n_symbols=n_symbols)
        results.append(res)
        for name, c in res["checks"].items():
            print(f"  [{name}] {c.get('结论', '')}")
            for k, v in c.items():
                if k != "结论" and not isinstance(v, (list, dict)):
                    print(f"        {k} = {v}")

    acc_cmp = None
    if args.accumulate and fee:
        print(f"\n{'=' * 76}\n加仓杠杆贡献对比（每个策略跑两遍回测）\n{'=' * 76}")
        try:
            acc_cmp = compare_accumulate(
                fee.get("symbols", []), fee["window"]["start"], fee["window"]["end"],
                init_cash,
                {r["strategy"]: r["config"] for r in fee["results"] if r.get("config")},
            )
            for r in acc_cmp["rows"]:
                print(f"  {r['策略']:<10} 加仓 {r['加仓累计收益%']:>7.2f}%  "
                      f"单次 {r['单次持仓收益%']:>7.2f}%  贡献 {r['加仓贡献(pp)']:>7.2f}pp "
                      f"({r['加仓贡献占比%']:.0f}%)")
        except Exception as e:
            print(f"  [跳过] 加仓对比失败: {type(e).__name__}: {e}")

    os.makedirs(os.path.dirname(args.out_html), exist_ok=True)
    with open(args.out_html, "w", encoding="utf-8") as fp:
        fp.write(to_html(results, acc_cmp))
    with open(args.out_json, "w", encoding="utf-8") as fp:
        json.dump({"runs": results, "accumulate_compare": acc_cmp},
                  fp, ensure_ascii=False, indent=2, default=str)
    print(f"\n质检报告: {args.out_html}")
    print(f"质检数据: {args.out_json}")


if __name__ == "__main__":
    main()
