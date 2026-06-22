"""Test writing pre-2003 daily data directly"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tushare as ts
import pandas as pd
from datetime import date
from data.database.connection import engine, SessionLocal
from sqlalchemy import text

pro = ts.pro_api('596dc52bf2356c6241077de51b61fea2c0ceeb1eebd6d78ec88e9832')

# Fetch 5 days of pre-2003 data
test_dates = ['19910102', '19910103', '19950103', '20000104', '20020102']
for td in test_dates:
    df = pro.daily(trade_date=td)
    if df is not None and not df.empty:
        # Convert dates
        df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d').dt.date
        
        # Write directly
        temp = '_tmp_test_daily'
        df.to_sql(temp, engine, if_exists='replace', index=False)
        
        with engine.connect() as conn:
            # Get column types
            col_types = {}
            for row in conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'daily' ORDER BY ordinal_position"
            )).fetchall():
                col_types[row[0]] = row[1]
            
            all_cols = [c for c in df.columns if c in col_types]
            
            # Build INSERT
            select_cols = []
            for c in all_cols:
                pg_type = col_types[c]
                if pg_type in ("double precision", "numeric", "real", "integer", "bigint", "smallint"):
                    select_cols.append(f"CAST({c} AS {pg_type})")
                else:
                    select_cols.append(c)
            
            insert_sql = f"INSERT INTO daily ({', '.join(all_cols)}) SELECT {', '.join(select_cols)} FROM {temp} ON CONFLICT (ts_code, trade_date) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in all_cols if c not in ('ts_code', 'trade_date'))}"
            
            result = conn.execute(text(insert_sql))
            conn.execute(text(f"DROP TABLE IF EXISTS {temp}"))
            conn.commit()
            print(f'{td}: inserted {len(df)} rows, rowcount={result.rowcount}')
    else:
        print(f'{td}: no data returned')

# Verify
with engine.connect() as conn:
    result = conn.execute(text("SELECT MIN(trade_date), COUNT(*) FROM daily WHERE trade_date < '2003-01-01'::date"))
    row = result.fetchone()
    print(f'\nVerification - min date before 2003: {row[0]}, count: {row[1]}')
