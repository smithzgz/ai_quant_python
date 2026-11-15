# -*- coding: utf-8 -*-
"""样本外(OOS)回测：消除"标的池前视选择偏差"。

原 run_fee_impact_backtest.py 的 universe 是"窗口内成交额 TopN"——这用到了窗口
内的未来信息(前视偏差)，会系统性高估策略收益。

本脚本改用两类"在窗口开始日即可知"的标的池做样本外回测：
  1) hs300    : 沪深300成分股(截至 2022-12-30 的成分，point-in-time，无前视)
  2) liq_pt20 : 起点前 200 个交易日内平均成交额 Top20(选股标准与原口径一致，
               只是把"窗口内总额"换成"起点前数据"，从而隔离出纯前视偏差)

成本模型沿用整改后的 A 股分边精确成本(precise_costs=True)。
产出: results/oos_backtest.json (含各 universe 各策略的 run_id 与绩效，并引用 IS 结果)。

用法:
    python scripts/run_oos_backtest.py
    python scripts/run_oos_backtest.py --start 2023-01-01 --end 2025-12-31 --cash 1000000
"""
import argparse
import datetime as dt
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from backtest.engine.vbt_engine import VBTEngine
from data.database.connection import engine, SessionLocal
from data.database.models import BacktestRun

BENCHMARK = "000300.SH"
DEFAULT_STRATEGIES = {
    "ma_cross": {"fast_window": 10, "slow_window": 50},
    "macd": {"fast_window": 12, "slow_window": 26, "signal_window": 9},
    "momentum": {"lookback": 20, "top_k": 5},
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- universe 构造 ----------

def load_hs300_asof(path: str = None) -> list:
    """读取已落盘的沪深300 point-in-time 成分(2022-12-30)。"""
    if path is None:
        path = os.path.join(ROOT, "data", "hs300_asof_20221230.json")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return list(d["constituents"])


def pick_liq_pt20(start: str, lookback_days: int = 200, min_bars: int = 60, top: int = 20) -> list:
    """起点前 lookback_days 窗口内的平均日成交额 Top20（无前视）。"""
    start_date = dt.date.fromisoformat(start)
    cutoff = (start_date - dt.timedelta(days=lookback_days)).isoformat()
    sql = text(
        """
        SELECT ts_code, AVG(amount) AS avg_amt, COUNT(*) AS bars
        FROM daily
        WHERE trade_date >= :cutoff AND trade_date < :start
        GROUP BY ts_code
        HAVING COUNT(*) >= :min_bars
        ORDER BY avg_amt DESC
        LIMIT :top
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql, {"cutoff": cutoff, "start": start, "min_bars": min_bars, "top": top}
        ).fetchall()
    return [r[0] for r in rows]


def filter_present(symbols: list, start: str, end: str) -> list:
    """仅保留窗口内确有日线数据的标的（数据可用性过滤，非收益前视）。"""
    if not symbols:
        return []
    sql = text(
        """
        SELECT DISTINCT ts_code FROM daily
        WHERE ts_code = ANY(:codes) AND trade_date BETWEEN :start AND :end
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"codes": list(symbols), "start": start, "end": end}).fetchall()
    present = {r[0] for r in rows}
    return [s for s in symbols if s in present]


# ---------- 基准 & 结果读取 ----------

def load_benchmark(start: str, end: str) -> dict:
    sql = text(
        """
        SELECT trade_date, close FROM index_daily
        WHERE ts_code = :code AND trade_date BETWEEN :start AND :end
        ORDER BY trade_date
        """
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, {"code": BENCHMARK, "start": start, "end": end}).fetchall()
    except Exception:
        return {}
    if not rows:
        return {}
    close = {r[0]: float(r[1]) for r in rows}
    dates = sorted(close)
    first, last = close[dates[0]], close[dates[-1]]
    rets = []
    prev = None
    for d in dates:
        if prev is not None:
            rets.append({"date": str(d), "ret": close[d] / prev - 1.0})
        prev = close[d]
    return {
        "code": BENCHMARK,
        "start": str(dates[0]),
        "end": str(dates[-1]),
        "total_return_pct": (last / first - 1.0) * 100.0,
        "daily_returns": rets,
    }


def fetch_run(run_id: int) -> dict:
    session = SessionLocal()
    try:
        run = session.get(BacktestRun, run_id)
        if not run:
            return {}
        return {
            "run_id": run.id,
            "total_return": run.total_return,
            "annual_return": run.annual_return,
            "max_drawdown": run.max_drawdown,
            "sharpe_ratio": run.sharpe_ratio,
            "win_rate": run.win_rate,
            "total_trades": run.total_trades,
            "final_value": run.final_value,
        }
    finally:
        session.close()


def load_is_precise(json_path: str) -> dict:
    """读取原 fee_impact.json 中 IS-top20 的精确成本结果，作为前视口径对照。"""
    if not os.path.exists(json_path):
        return {}
    with open(json_path, encoding="utf-8") as f:
        d = json.load(f)
    out = {}
    for r in d.get("results", []):
        p = r.get("precise", {})
        if "total_return" in p:
            out[r["strategy"]] = p
    return out


# ---------- 主流程 ----------

def run_universe(label: str, symbols: list, start: str, end: str, cash: float) -> dict:
    bt = VBTEngine()
    out = {"label": label, "n_symbols": len(symbols), "symbols": symbols, "strategies": {}}
    for name, cfg in DEFAULT_STRATEGIES.items():
        try:
            rid = bt.run(
                strategy_name=name, symbols=symbols, start=start, end=end,
                strategy_config=cfg, init_cash=cash, precise_costs=True,
            )
            out["strategies"][name] = fetch_run(rid)
            print(f"  [{label}] {name}: run_id={rid} 收益={out['strategies'][name].get('total_return')}")
        except Exception as e:
            out["strategies"][name] = {"error": str(e)}
            print(f"  [{label}] {name} 失败: {e}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--cash", type=float, default=1_000_000.0)
    ap.add_argument("--out", default="results/oos_backtest.json")
    args = ap.parse_args()

    print("=" * 78)
    print("样本外(OOS)回测：消除标的池前视选择偏差")
    print("=" * 78)
    print(f"窗口: {args.start} ~ {args.end}  初始资金: {args.cash:,.0f}")

    hs300 = filter_present(load_hs300_asof(), args.start, args.end)
    liq = filter_present(pick_liq_pt20(args.start), args.start, args.end)
    print(f"HS300(point-in-time) 标的数: {len(hs300)}")
    print(f"流动性Top20(point-in-time) 标的数: {len(liq)}")

    bench = load_benchmark(args.start, args.end)
    if bench:
        print(f"基准 {bench['code']}: {bench['total_return_pct']:.2f}%")

    oos = {
        "hs300": run_universe("沪深300成分(2022-12-30 point-in-time)", hs300, args.start, args.end, args.cash),
        "liq_pt20": run_universe("起点前200日流动性Top20(point-in-time)", liq, args.start, args.end, args.cash),
    }

    is_precise = load_is_precise(os.path.join(ROOT, "results", "fee_impact.json"))

    payload = {
        "window": {"start": args.start, "end": args.end},
        "init_cash": args.cash,
        "benchmark": bench,
        "is_top20_precise": is_precise,
        "oos": oos,
        "note": (
            "is_top20_precise = 原口径(窗口内成交额Top20, 含前视偏差); "
            "oos.hs300 = 沪深300成分(起点日point-in-time); "
            "oos.liq_pt20 = 起点前流动性Top20(point-in-time, 与is同选股标准, "
            "用于隔离纯前视偏差)。三者成本模型均为A股分边精确成本。"
        ),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已写入: {args.out}")


if __name__ == "__main__":
    main()
