"""Optimized: Pre-compute latest adj_factor, then batch populate"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.database.connection import engine
from sqlalchemy import text

with engine.connect() as conn:
    t0 = time.time()

    # Step 1: Create temp table with latest adj_factor per stock
    print("Step 1: Creating temp_latest_af...")
    conn.execute(text("DROP TABLE IF EXISTS _tmp_latest_af"))
    conn.execute(text("""
        CREATE TEMP TABLE _tmp_latest_af AS
        SELECT ts_code, MAX(adj_factor) AS max_af
        FROM adj_factor
        GROUP BY ts_code
    """))
    conn.commit()
    cur = conn.execute(text("SELECT COUNT(*) FROM _tmp_latest_af"))
    print(f"  {cur.fetchone()[0]} stocks")

    # Step 2: Create daily_qfq (前复权)
    print("Step 2: Populating daily_qfq (前复权)...")
    conn.execute(text("TRUNCATE TABLE daily_qfq"))
    conn.execute(text("""
        INSERT INTO daily_qfq (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor)
        SELECT
            d.ts_code, d.trade_date,
            ROUND((d.open * af.adj_factor / lt.max_af)::numeric, 4),
            ROUND((d.high * af.adj_factor / lt.max_af)::numeric, 4),
            ROUND((d.low * af.adj_factor / lt.max_af)::numeric, 4),
            ROUND((d.close * af.adj_factor / lt.max_af)::numeric, 4),
            ROUND((d.pre_close * af.adj_factor / lt.max_af)::numeric, 4),
            ROUND((d.change * af.adj_factor / lt.max_af)::numeric, 4),
            d.pct_chg, d.vol,
            ROUND((d.amount * af.adj_factor / lt.max_af)::numeric, 4),
            af.adj_factor
        FROM daily d
        JOIN adj_factor af ON d.ts_code = af.ts_code AND d.trade_date = af.trade_date
        JOIN _tmp_latest_af lt ON d.ts_code = lt.ts_code
        WHERE af.adj_factor > 0 AND lt.max_af > 0
    """))
    conn.commit()
    cur = conn.execute(text("SELECT COUNT(*) FROM daily_qfq"))
    print(f"  daily_qfq: {cur.fetchone()[0]:,} rows ({time.time()-t0:.0f}s)")

    # Step 3: Create daily_hfq (后复权)
    print("Step 3: Populating daily_hfq (后复权)...")
    conn.execute(text("TRUNCATE TABLE daily_hfq"))
    conn.execute(text("""
        INSERT INTO daily_hfq (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor)
        SELECT
            d.ts_code, d.trade_date,
            ROUND((d.open * af.adj_factor)::numeric, 4),
            ROUND((d.high * af.adj_factor)::numeric, 4),
            ROUND((d.low * af.adj_factor)::numeric, 4),
            ROUND((d.close * af.adj_factor)::numeric, 4),
            ROUND((d.pre_close * af.adj_factor)::numeric, 4),
            ROUND((d.change * af.adj_factor)::numeric, 4),
            d.pct_chg, d.vol,
            ROUND((d.amount * af.adj_factor)::numeric, 4),
            af.adj_factor
        FROM daily d
        JOIN adj_factor af ON d.ts_code = af.ts_code AND d.trade_date = af.trade_date
        WHERE af.adj_factor > 0
    """))
    conn.commit()
    cur = conn.execute(text("SELECT COUNT(*) FROM daily_hfq"))
    print(f"  daily_hfq: {cur.fetchone()[0]:,} rows ({time.time()-t0:.0f}s)")

    # Step 4: Create hypertables
    print("Step 4: Creating hypertables...")
    for tbl in ['daily_qfq', 'daily_hfq']:
        try:
            conn.execute(text(f"SELECT create_hypertable('{tbl}', 'trade_date', if_not_exists => TRUE)"))
            print(f"  {tbl}: hypertable OK")
        except Exception as e:
            print(f"  {tbl}: {e}")

    # Step 5: Verify
    print("\n=== Verification ===")
    for tbl in ['daily', 'daily_qfq', 'daily_hfq']:
        cur = conn.execute(text(f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM {tbl}"))
        r = cur.fetchone()
        print(f"  {tbl:12s}: {r[0]:>12,} rows, {r[1]} ~ {r[2]}")

    print("\n=== Price Comparison (000001.SZ) ===")
    for tbl in ['daily', 'daily_qfq', 'daily_hfq']:
        cur = conn.execute(text(
            f"SELECT trade_date, open, close FROM {tbl} WHERE ts_code='000001.SZ' AND trade_date='20260618'::date"
        ))
        row = cur.fetchone()
        if row:
            print(f"  {tbl:12s}: {row[0]} O={row[1]:.4f} C={row[2]:.4f}")

    conn.execute(text("DROP TABLE IF EXISTS _tmp_latest_af"))
    conn.commit()
    print(f"\nTotal: {time.time()-t0:.0f}s")
