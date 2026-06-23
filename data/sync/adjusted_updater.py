# -*- coding: utf-8 -*-
"""Incrementally update daily_qfq and daily_hfq after daily/adj_factor sync."""
from datetime import date
from sqlalchemy import text
from data.database.connection import engine
from utils.logger import get_logger

logger = get_logger("adjusted_updater")


def update_adjusted_tables(synced_start: date = None, synced_end: date = None):
    """Update daily_qfq and daily_hfq incrementally for the date range."""
    if not synced_start or not synced_end:
        logger.warning("adjusted_updater: no date range, skipping")
        return

    start_str = synced_start.strftime("%Y-%m-%d") if isinstance(synced_start, date) else str(synced_start)
    end_str = synced_end.strftime("%Y-%m-%d") if isinstance(synced_end, date) else str(synced_end)

    logger.info(f"adjusted_updater: updating {start_str} ~ {end_str}")

    with engine.begin() as conn:
        # 前复权: price * adj_factor / latest_adj_factor
        conn.execute(text("""
            INSERT INTO daily_qfq (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor)
            SELECT
                d.ts_code, d.trade_date,
                ROUND((d.open * af.adj_factor / lf.adj_factor)::numeric, 4),
                ROUND((d.high * af.adj_factor / lf.adj_factor)::numeric, 4),
                ROUND((d.low * af.adj_factor / lf.adj_factor)::numeric, 4),
                ROUND((d.close * af.adj_factor / lf.adj_factor)::numeric, 4),
                ROUND((d.pre_close * af.adj_factor / lf.adj_factor)::numeric, 4),
                ROUND(((d.close - d.pre_close) * af.adj_factor / lf.adj_factor)::numeric, 4),
                d.pct_chg, d.vol, d.amount, af.adj_factor
            FROM daily d
            JOIN adj_factor af ON d.ts_code = af.ts_code AND d.trade_date = af.trade_date
            JOIN adj_factor lf ON d.ts_code = lf.ts_code AND lf.trade_date = (SELECT MAX(trade_date) FROM adj_factor)
            WHERE d.trade_date BETWEEN :s AND :e
            ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                close = EXCLUDED.close, pre_close = EXCLUDED.pre_close,
                change = EXCLUDED.change, pct_chg = EXCLUDED.pct_chg,
                vol = EXCLUDED.vol, amount = EXCLUDED.amount, adj_factor = EXCLUDED.adj_factor
        """), {"s": start_str, "e": end_str})

        qfq_count = conn.execute(text(
            "SELECT COUNT(*) FROM daily_qfq WHERE trade_date BETWEEN :s AND :e"
        ), {"s": start_str, "e": end_str}).scalar()

        # 后复权: price * adj_factor
        conn.execute(text("""
            INSERT INTO daily_hfq (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor)
            SELECT
                d.ts_code, d.trade_date,
                ROUND((d.open * af.adj_factor)::numeric, 4) AS open,
                ROUND((d.high * af.adj_factor)::numeric, 4) AS high,
                ROUND((d.low * af.adj_factor)::numeric, 4) AS low,
                ROUND((d.close * af.adj_factor)::numeric, 4) AS close,
                ROUND((d.pre_close * af.adj_factor)::numeric, 4) AS pre_close,
                ROUND(((d.close - d.pre_close) * af.adj_factor)::numeric, 4) AS change,
                d.pct_chg, d.vol, d.amount, af.adj_factor
            FROM daily d
            JOIN adj_factor af ON d.ts_code = af.ts_code AND d.trade_date = af.trade_date
            WHERE d.trade_date BETWEEN :s AND :e
            ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                close = EXCLUDED.close, pre_close = EXCLUDED.pre_close,
                change = EXCLUDED.change, pct_chg = EXCLUDED.pct_chg,
                vol = EXCLUDED.vol, amount = EXCLUDED.amount, adj_factor = EXCLUDED.adj_factor
        """), {"s": start_str, "e": end_str})

        hfq_count = conn.execute(text(
            "SELECT COUNT(*) FROM daily_hfq WHERE trade_date BETWEEN :s AND :e"
        ), {"s": start_str, "e": end_str}).scalar()

    logger.info(f"adjusted_updater: done. qfq={qfq_count}, hfq={hfq_count} rows updated")
