"""Re-sync daily data for dates before 2003-06-17 (missing due to connection error)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import tushare as ts
import pandas as pd
from datetime import date, datetime
from data.database.connection import engine, SessionLocal
from sqlalchemy import text
from config.data_sync_config import DATA_SYNC_TASKS

pro = ts.pro_api('596dc52bf2356c6241077de51b61fea2c0ceeb1eebd6d78ec88e9832')
cfg = DATA_SYNC_TASKS['daily']
fields_str = ",".join(cfg["fields"].keys())

# Get trade dates before 2003-06-17
with engine.connect() as conn:
    result = conn.execute(text(
        "SELECT cal_date FROM trade_cal WHERE is_open = 1 AND cal_date < :cutoff ORDER BY cal_date"
    ), {"cutoff": date(2003, 6, 17)})
    trade_dates = [row[0] for row in result.fetchall()]

print(f"Found {len(trade_dates)} trade dates to re-sync (before 2003-06-17)")

# Check which dates already have data
with engine.connect() as conn:
    result = conn.execute(text(
        "SELECT trade_date FROM daily WHERE trade_date < :cutoff GROUP BY trade_date"
    ), {"cutoff": date(2003, 6, 17)})
    existing = {row[0] for row in result.fetchall()}

missing = [td for td in trade_dates if td not in existing]
print(f"Already have {len(existing)} dates, missing {len(missing)} dates")

if not missing:
    print("All pre-2003 dates already have data. Nothing to do.")
    sys.exit(0)

total_rows = 0
errors = 0

for i, td in enumerate(missing):
    td_str = td.strftime("%Y%m%d")
    try:
        df = pro.daily(trade_date=td_str)
        time.sleep(0.35)  # Rate limit

        if df is None or df.empty:
            continue

        # Convert dates
        df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d').dt.date

        # Write using same logic as engine
        temp = '_tmp_recovery_daily'
        df.to_sql(temp, engine, if_exists='replace', index=False)

        with engine.connect() as conn:
            col_types = {}
            for row in conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'daily' ORDER BY ordinal_position"
            )).fetchall():
                col_types[row[0]] = row[1]

            all_cols = [c for c in df.columns if c in col_types]
            if not all_cols:
                continue

            select_cols = []
            for c in all_cols:
                pg_type = col_types[c]
                if pg_type in ("double precision", "numeric", "real", "integer", "bigint", "smallint"):
                    select_cols.append(f"CAST({c} AS {pg_type})")
                else:
                    select_cols.append(c)

            pk_cols = ['ts_code', 'trade_date']
            set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in all_cols if c not in pk_cols)
            insert_sql = (
                f"INSERT INTO daily ({', '.join(all_cols)}) "
                f"SELECT {', '.join(select_cols)} FROM {temp} "
                f"ON CONFLICT ({', '.join(pk_cols)}) DO UPDATE SET {set_clause}"
            )
            conn.execute(text(insert_sql))
            conn.execute(text(f"DROP TABLE IF EXISTS {temp}"))
            conn.commit()

        total_rows += len(df)
        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(missing)}] {td_str}: {len(df)} rows (total: {total_rows})")

    except Exception as e:
        errors += 1
        print(f"  ERROR {td_str}: {e}")
        if errors > 10:
            print("Too many errors, stopping")
            break

print(f"\nDone: {total_rows} rows inserted across {len(missing) - errors} dates ({errors} errors)")

# Verify
with engine.connect() as conn:
    result = conn.execute(text(
        "SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date) FROM daily WHERE trade_date < :cutoff"
    ), {"cutoff": date(2003, 6, 17)})
    row = result.fetchone()
    print(f"Pre-2003 data: min={row[0]}, max={row[1]}, distinct_dates={row[2]}")
