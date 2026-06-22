# -*- coding: utf-8 -*-
from sqlalchemy import text
from data.database.connection import engine
from utils.logger import get_logger

logger = get_logger("queries")


QUERIES = {
    "backtest_summary": """
        SELECT id, strategy_name, total_return, annual_return, max_drawdown,
               sharpe_ratio, win_rate, total_trades, final_value, status, created_at
        FROM backtest_runs
        ORDER BY created_at DESC
        LIMIT 20
    """,
    "equity_curve": """
        SELECT timestamp, equity_value, drawdown, daily_return
        FROM equity_curves
        WHERE run_id = :run_id
        ORDER BY timestamp
    """,
    "trade_list": """
        SELECT symbol, direction, entry_time, exit_time, entry_price, exit_price,
               size, pnl, return_pct, fees, duration_bars
        FROM trade_records
        WHERE run_id = :run_id
        ORDER BY entry_time
    """,
    "trade_pnl_distribution": """
        SELECT
            CASE WHEN pnl > 0 THEN 'win' ELSE 'loss' END as side,
            COUNT(*) as count,
            AVG(pnl) as avg_pnl,
            SUM(pnl) as total_pnl
        FROM trade_records
        WHERE run_id = :run_id
        GROUP BY side
    """,
    "monthly_returns": """
        SELECT
            EXTRACT(YEAR FROM timestamp) as year,
            EXTRACT(MONTH FROM timestamp) as month,
            SUM(daily_return) as monthly_return
        FROM equity_curves
        WHERE run_id = :run_id
        GROUP BY year, month
        ORDER BY year, month
    """,
    "data_sync_status": """
        SELECT table_name, last_sync_date, updated_at
        FROM sync_checkpoint
        ORDER BY updated_at DESC
    """,
    "data_quality_latest": """
        SELECT table_name, rule_name, status, total_rows, issue_count, checked_at
        FROM data_quality_log
        WHERE checked_at > NOW() - INTERVAL '7 days'
        ORDER BY checked_at DESC
    """,
}


def execute_query(name: str, params: dict = None):
    sql = QUERIES.get(name)
    if not sql:
        raise ValueError(f"Unknown query: {name}")

    import pandas as pd
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})
