"""Create daily_qfq (前复权) and daily_hfq (后复权) tables from daily + adj_factor"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from data.database.connection import engine, SessionLocal
from sqlalchemy import text

print("=== Creating daily_qfq and daily_hfq tables ===")

with engine.connect() as conn:
    # 1. Create daily_qfq (前复权) table
    print("Creating daily_qfq table...")
    conn.execute(text("""
        DROP TABLE IF EXISTS daily_qfq CASCADE;
    """))
    conn.execute(text("""
        CREATE TABLE daily_qfq (
            ts_code VARCHAR(20) NOT NULL,
            trade_date DATE NOT NULL,
            open DOUBLE PRECISION,
            high DOUBLE PRECISION,
            low DOUBLE PRECISION,
            close DOUBLE PRECISION,
            pre_close DOUBLE PRECISION,
            change DOUBLE PRECISION,
            pct_chg DOUBLE PRECISION,
            vol DOUBLE PRECISION,
            amount DOUBLE PRECISION,
            adj_factor DOUBLE PRECISION,
            PRIMARY KEY (ts_code, trade_date)
        );
    """))
    conn.commit()
    print("  daily_qfq table created")

    # 2. Create daily_hfq (后复权) table
    print("Creating daily_hfq table...")
    conn.execute(text("""
        DROP TABLE IF EXISTS daily_hfq CASCADE;
    """))
    conn.execute(text("""
        CREATE TABLE daily_hfq (
            ts_code VARCHAR(20) NOT NULL,
            trade_date DATE NOT NULL,
            open DOUBLE PRECISION,
            high DOUBLE PRECISION,
            low DOUBLE PRECISION,
            close DOUBLE PRECISION,
            pre_close DOUBLE PRECISION,
            change DOUBLE PRECISION,
            pct_chg DOUBLE PRECISION,
            vol DOUBLE PRECISION,
            amount DOUBLE PRECISION,
            adj_factor DOUBLE PRECISION,
            PRIMARY KEY (ts_code, trade_date)
        );
    """))
    conn.commit()
    print("  daily_hfq table created")

    # 3. Populate daily_qfq (前复权)
    # 前复权: price × (adj_factor / latest_adj_factor)
    # latest_adj_factor = adj_factor on the most recent trading date for each stock
    print("Populating daily_qfq (前复权)...")
    conn.execute(text("""
        INSERT INTO daily_qfq (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor)
        SELECT
            d.ts_code,
            d.trade_date,
            ROUND((d.open * af.adj_factor / latest.max_af)::numeric, 4)::double precision AS open,
            ROUND((d.high * af.adj_factor / latest.max_af)::numeric, 4)::double precision AS high,
            ROUND((d.low * af.adj_factor / latest.max_af)::numeric, 4)::double precision AS low,
            ROUND((d.close * af.adj_factor / latest.max_af)::numeric, 4)::double precision AS close,
            ROUND((d.pre_close * af.adj_factor / latest.max_af)::numeric, 4)::double precision AS pre_close,
            ROUND((d.change * af.adj_factor / latest.max_af)::numeric, 4)::double precision AS change,
            d.pct_chg,
            d.vol,
            ROUND((d.amount * af.adj_factor / latest.max_af)::numeric, 4)::double precision AS amount,
            af.adj_factor
        FROM daily d
        JOIN adj_factor af ON d.ts_code = af.ts_code AND d.trade_date = af.trade_date
        JOIN (
            SELECT ts_code, MAX(adj_factor) AS max_af
            FROM adj_factor
            GROUP BY ts_code
        ) latest ON d.ts_code = latest.ts_code
        WHERE af.adj_factor > 0 AND latest.max_af > 0
    """))
    conn.commit()
    print("  daily_qfq populated")

    # 4. Populate daily_hfq (后复权)
    # 后复权: price × adj_factor
    print("Populating daily_hfq (后复权)...")
    conn.execute(text("""
        INSERT INTO daily_hfq (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor)
        SELECT
            d.ts_code,
            d.trade_date,
            ROUND((d.open * af.adj_factor)::numeric, 4)::double precision AS open,
            ROUND((d.high * af.adj_factor)::numeric, 4)::double precision AS high,
            ROUND((d.low * af.adj_factor)::numeric, 4)::double precision AS low,
            ROUND((d.close * af.adj_factor)::numeric, 4)::double precision AS close,
            ROUND((d.pre_close * af.adj_factor)::numeric, 4)::double precision AS pre_close,
            ROUND((d.change * af.adj_factor)::numeric, 4)::double precision AS change,
            d.pct_chg,
            d.vol,
            ROUND((d.amount * af.adj_factor)::numeric, 4)::double precision AS amount,
            af.adj_factor
        FROM daily d
        JOIN adj_factor af ON d.ts_code = af.ts_code AND d.trade_date = af.trade_date
        WHERE af.adj_factor > 0
    """))
    conn.commit()
    print("  daily_hfq populated")

    # 5. Verify
    print("\n=== Verification ===")
    for tbl in ['daily', 'daily_qfq', 'daily_hfq']:
        result = conn.execute(text(f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM {tbl}"))
        row = result.fetchone()
        print(f"  {tbl:12s}: {row[0]:>12,} rows, {row[1]} ~ {row[2]}")

    # Verify prices for 000001.SZ at a specific date
    print("\n=== Price Comparison (000001.SZ 20260618) ===")
    for tbl in ['daily', 'daily_qfq', 'daily_hfq']:
        result = conn.execute(text(
            f"SELECT open, high, low, close FROM {tbl} WHERE ts_code='000001.SZ' AND trade_date='20260618'::date"
        ))
        row = result.fetchone()
        if row:
            print(f"  {tbl:12s}: O={row[0]:.4f} H={row[1]:.4f} L={row[2]:.4f} C={row[3]:.4f}")

    # Create TimescaleDB hypertable if applicable
    print("\n=== TimescaleDB hypertables ===")
    for tbl in ['daily_qfq', 'daily_hfq']:
        try:
            conn.execute(text(f"SELECT create_hypertable('{tbl}', 'trade_date', if_not_exists => TRUE)"))
            print(f"  {tbl}: hypertable created")
        except Exception as e:
            print(f"  {tbl}: {e}")

print("\n=== Done ===")
