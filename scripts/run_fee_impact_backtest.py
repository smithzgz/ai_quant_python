# -*- coding: utf-8 -*-
"""费用模型影响回测：对比"旧单一比例费(漏印花税)"与"A股分边精确成本"的绩效差异。

用法:
    python scripts/run_fee_impact_backtest.py
    python scripts/run_fee_impact_backtest.py --start 2023-01-01 --end 2025-12-31 --top 20 --cash 1000000

产出: 控制台对比表 + results/fee_impact.json（供 gen_backtest_report.py 生成 HTML 报告）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from backtest.engine.vbt_engine import VBTEngine
from data.database.connection import engine, SessionLocal
from data.database.models import BacktestRun
from utils.logger import get_logger

logger = get_logger("fee_impact")

BENCHMARK = "000300.SH"      # 沪深300
DEFAULT_STRATEGIES = {
    "ma_cross": {"fast_window": 10, "slow_window": 50},
    "macd": {"fast_window": 12, "slow_window": 26, "signal_window": 9},
    "momentum": {"lookback": 20, "top_k": 5},
}


def pick_universe(start: str, end: str, top: int, min_bars: int = 600) -> list:
    """窗口内成交额最大的 top 只股票（要求有足够 bar，剔除次新股/长期停牌）。"""
    sql = text(
        """
        SELECT ts_code, count(*) AS bars, sum(amount) AS amt
        FROM daily
        WHERE trade_date BETWEEN :start AND :end
        GROUP BY ts_code
        HAVING count(*) >= :min_bars
        ORDER BY sum(amount) DESC
        LIMIT :top
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql, {"start": start, "end": end, "min_bars": min_bars, "top": top}
        ).fetchall()
    return [r[0] for r in rows]


def load_benchmark(start: str, end: str) -> dict:
    """读沪深300收盘，算窗口总收益与日收益序列。"""
    sql = text(
        """
        SELECT trade_date, close FROM index_daily
        WHERE ts_code = :code AND trade_date BETWEEN :start AND :end
        ORDER BY trade_date
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"code": BENCHMARK, "start": start, "end": end}).fetchall()
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--top", type=int, default=20, help="流动性前 N 只")
    ap.add_argument("--cash", type=float, default=1_000_000.0)
    ap.add_argument("--out", default="results/fee_impact.json")
    args = ap.parse_args()

    print("=" * 78)
    print("费用模型影响回测：旧单一比例费(漏印花税) vs A股分边精确成本")
    print("=" * 78)

    symbols = pick_universe(args.start, args.end, args.top)
    print(f"\n窗口: {args.start} ~ {args.end}")
    print(f"初始资金: {args.cash:,.0f} 元")
    print(f"标的池: 流动性前 {len(symbols)} 只 -> {symbols}")

    bench = load_benchmark(args.start, args.end)
    if bench:
        print(f"基准 {bench['code']}: {bench['total_return_pct']:.2f}%")

    bt = VBTEngine()
    results = []

    for name, cfg in DEFAULT_STRATEGIES.items():
        print(f"\n--- {name} {cfg} ---")
        row = {"strategy": name, "config": cfg}

        # 新：A股分边精确成本（含卖出印花税 + 最低佣金）
        try:
            rid_new = bt.run(
                strategy_name=name, symbols=symbols, start=args.start, end=args.end,
                strategy_config=cfg, init_cash=args.cash, precise_costs=True,
            )
            row["precise"] = fetch_run(rid_new)
            print(f"  精确成本: run_id={rid_new} 收益={row['precise'].get('total_return')}")
        except Exception as e:
            logger.error(f"{name} precise failed: {e}")
            row["precise"] = {"error": str(e)}
            print(f"  精确成本失败: {e}")

        # 旧：单一比例费（佣金+过户费，无印花税）
        try:
            rid_old = bt.run(
                strategy_name=name, symbols=symbols, start=args.start, end=args.end,
                strategy_config=cfg, init_cash=args.cash, commission_rate=0.00026,
            )
            row["flat"] = fetch_run(rid_old)
            print(f"  旧口径  : run_id={rid_old} 收益={row['flat'].get('total_return')}")
        except Exception as e:
            logger.error(f"{name} flat failed: {e}")
            row["flat"] = {"error": str(e)}
            print(f"  旧口径失败: {e}")

        p, f = row.get("precise", {}), row.get("flat", {})
        if "total_return" in p and "total_return" in f:
            row["delta_return_pp"] = f["total_return"] - p["total_return"]
            row["delta_value"] = (f.get("final_value") or 0) - (p.get("final_value") or 0)
            print(f"  差异: 收益高估 {row['delta_return_pp']:.3f} 个百分点, "
                  f"终值高估 {row['delta_value']:,.0f} 元")

        results.append(row)

    # 汇总表
    print("\n" + "=" * 78)
    print(f"{'策略':<12}{'旧口径收益%':>12}{'精确成本收益%':>14}{'高估(pp)':>11}{'交易数':>9}{'夏普':>8}")
    print("-" * 78)
    for r in results:
        p, f = r.get("precise", {}), r.get("flat", {})
        if "total_return" in p and "total_return" in f:
            print(f"{r['strategy']:<12}{f['total_return']:>12.2f}{p['total_return']:>14.2f}"
                  f"{r['delta_return_pp']:>11.3f}{p.get('total_trades', 0):>9}"
                  f"{(p.get('sharpe_ratio') or 0):>8.2f}")
        else:
            print(f"{r['strategy']:<12}{'ERR':>12}{'ERR':>14}")
    if bench:
        print("-" * 78)
        print(f"{'基准300':<12}{bench['total_return_pct']:>12.2f}")
    print("=" * 78)

    payload = {
        "window": {"start": args.start, "end": args.end},
        "init_cash": args.cash,
        "symbols": symbols,
        "benchmark": bench,
        "results": results,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已写入: {args.out}")


if __name__ == "__main__":
    main()
