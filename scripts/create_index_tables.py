# -*- coding: utf-8 -*-
"""Create index tables for tushare index data."""
import sys
sys.path.insert(0, r"D:\code\Python\ai_quant_python")

from sqlalchemy import text
from data.database.connection import engine

tables = [
    # 1. index_basic
    """
    CREATE TABLE IF NOT EXISTS index_basic (
        ts_code VARCHAR(20) PRIMARY KEY,
        name VARCHAR(100),
        market VARCHAR(10),
        publisher VARCHAR(100),
        category VARCHAR(50),
        base_date VARCHAR(10),
        base_point FLOAT,
        list_date VARCHAR(10)
    )
    """,
    # 2. index_daily
    """
    CREATE TABLE IF NOT EXISTS index_daily (
        ts_code VARCHAR(20) NOT NULL,
        trade_date DATE NOT NULL,
        close FLOAT,
        open FLOAT,
        high FLOAT,
        low FLOAT,
        pre_close FLOAT,
        change FLOAT,
        pct_chg FLOAT,
        vol FLOAT,
        amount FLOAT,
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    # 3. index_weekly
    """
    CREATE TABLE IF NOT EXISTS index_weekly (
        ts_code VARCHAR(20) NOT NULL,
        trade_date DATE NOT NULL,
        close FLOAT,
        open FLOAT,
        high FLOAT,
        low FLOAT,
        pre_close FLOAT,
        change FLOAT,
        pct_chg FLOAT,
        vol FLOAT,
        amount FLOAT,
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    # 4. index_monthly
    """
    CREATE TABLE IF NOT EXISTS index_monthly (
        ts_code VARCHAR(20) NOT NULL,
        trade_date DATE NOT NULL,
        close FLOAT,
        open FLOAT,
        high FLOAT,
        low FLOAT,
        pre_close FLOAT,
        change FLOAT,
        pct_chg FLOAT,
        vol FLOAT,
        amount FLOAT,
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    # 5. index_weight
    """
    CREATE TABLE IF NOT EXISTS index_weight (
        index_code VARCHAR(20) NOT NULL,
        con_code VARCHAR(20) NOT NULL,
        trade_date DATE NOT NULL,
        weight FLOAT,
        PRIMARY KEY (index_code, con_code, trade_date)
    )
    """,
    # 6. index_dailybasic
    """
    CREATE TABLE IF NOT EXISTS index_dailybasic (
        ts_code VARCHAR(20) NOT NULL,
        trade_date DATE NOT NULL,
        total_mv FLOAT,
        float_mv FLOAT,
        total_share FLOAT,
        float_share FLOAT,
        free_share FLOAT,
        turnover_rate FLOAT,
        turnover_rate_f FLOAT,
        pe FLOAT,
        pe_ttm FLOAT,
        pb FLOAT,
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
]

with engine.begin() as conn:
    for sql in tables:
        conn.execute(text(sql))
        table_name = sql.strip().split("\n")[0].split("EXISTS ")[-1].split(" ")[0]
        print(f"Created: {table_name}")

print("All index tables created.")
