# -*- coding: utf-8 -*-
"""全量历史数据多策略回测"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from data.database.connection import engine, SessionLocal
from data.database.models import BacktestRun
from backtest.engine.vbt_engine import VBTEngine
from backtest.strategies.registry import StrategyRegistry
from utils.logger import get_logger
from sqlalchemy import text

logger = get_logger("backtest_all")


def get_symbols(count=50):
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT ts_code FROM daily_basic "
            "WHERE trade_date = (SELECT MAX(trade_date) FROM daily_basic) "
            "AND total_mv IS NOT NULL "
            "ORDER BY total_mv DESC LIMIT :count"
        ), {"count": count}).fetchall()
    return [r[0] for r in result]


def run_all():
    bt = VBTEngine()
    symbols = get_symbols(50)
    logger.info(f"Selected {len(symbols)} symbols by market cap")

    with engine.connect() as conn:
        r = conn.execute(text("SELECT MIN(trade_date), MAX(trade_date) FROM daily")).fetchone()
    start, end = str(r[0]), str(r[1])
    logger.info(f"Date range: {start} ~ {end}")

    strategies = StrategyRegistry.list_all()
    logger.info(f"Strategies to run: {strategies}")

    for name in strategies:
        try:
            logger.info(f"\n{'='*50}\n  Running: {name}\n{'='*50}")
            cfg = StrategyRegistry.get(name).get_default_config()
            run_id = bt.run(
                strategy_name=name,
                symbols=symbols,
                start=start,
                end=end,
                strategy_config=cfg,
                init_cash=100000.0,
            )
            session = SessionLocal()
            try:
                run = session.query(BacktestRun).get(run_id)
                sr = f"{run.sharpe_ratio:.2f}" if run.sharpe_ratio is not None else "N/A"
                logger.info(
                    f"{name}: return={run.total_return:.2f}% | "
                    f"max_dd={run.max_drawdown:.2f}% | "
                    f"sharpe={sr} | "
                    f"trades={run.total_trades} | "
                    f"final={run.final_value:.2f}"
                )
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Strategy {name} failed: {e}")
            import traceback
            traceback.print_exc()

    logger.info("\n\n===== ALL BACKTESTS COMPLETE =====")
    session = SessionLocal()
    try:
        runs = session.query(BacktestRun).order_by(BacktestRun.id.desc()).limit(10).all()
        for r in runs:
            sr = f"{r.sharpe_ratio:.2f}" if r.sharpe_ratio is not None else "N/A"
            logger.info(
                f"Run#{r.id} | {r.strategy_name} | "
                f"return={r.total_return:.2f}% | "
                f"max_dd={r.max_drawdown:.2f}% | "
                f"sharpe={sr} | "
                f"trades={r.total_trades}"
            )
    finally:
        session.close()


if __name__ == "__main__":
    run_all()
