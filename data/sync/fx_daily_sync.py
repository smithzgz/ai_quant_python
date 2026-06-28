# -*- coding: utf-8 -*-
"""
外汇日线行情自定义同步函数
Tushare fx_daily API 每次最多返回 4000 行（最新数据），
需要分段调用获取全量历史数据。
"""
import time
import logging
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)


def sync_fx_daily(db_conn, mode: str = 'full', max_pages: int = 0,
                  batch_size: int = 50, **kwargs) -> dict:
    """
    Sync fx_daily data with 2-pass approach per code to get full history.
    
    Pass 1: Latest 4000 rows (no date filter)
    Pass 2: Older data with start_date='20000101' and end_date=earliest-1day
    """
    from data.sync.tushare_client import TushareClient
    client = TushareClient()

    # Get all ts_codes from fx_obasic
    cursor = db_conn.cursor()
    cursor.execute("SELECT ts_code FROM fx_obasic ORDER BY ts_code")
    codes = [row[0] for row in cursor.fetchall()]
    cursor.close()

    if not codes:
        logger.warning("fx_daily: no codes found in fx_obasic")
        return {"total_rows": 0, "codes": 0}

    logger.info(f"fx_daily: starting full sync for {len(codes)} codes")

    # Get field list from config
    from config.data_sync_config import DATA_SYNC_TASKS
    cfg = DATA_SYNC_TASKS.get("fx_daily", {})
    fields_str = ",".join(cfg.get("fields", {}).keys())
    pk_cols = ["ts_code", "trade_date"]

    total_rows = 0
    success_codes = 0
    fail_codes = 0

    for i, ts_code in enumerate(codes):
        try:
            code_rows = 0

            # Pass 1: Get latest 4000 rows
            data = client.call("fx_daily", ts_code=ts_code, fields=fields_str)
            if data is not None and not data.empty:
                earliest = data["trade_date"].min()
                _write_upsert(db_conn, "fx_daily", data, pk_cols)
                code_rows += len(data)
                logger.info(f"fx_daily {ts_code}: pass1 got {len(data)} rows, earliest={earliest}")
            else:
                earliest = None
                logger.info(f"fx_daily {ts_code}: pass1 got 0 rows")

            # Pass 2: Get older data if pass1 returned a full page
            if code_rows >= 4000 and earliest is not None:
                if hasattr(earliest, 'strftime'):
                    end_date = (earliest - timedelta(days=1)).strftime("%Y%m%d")
                else:
                    end_date = str(earliest)

                data2 = client.call("fx_daily", ts_code=ts_code,
                                    start_date="20000101", end_date=end_date,
                                    fields=fields_str)
                if data2 is not None and not data2.empty:
                    _write_upsert(db_conn, "fx_daily", data2, pk_cols)
                    code_rows += len(data2)
                    logger.info(f"fx_daily {ts_code}: pass2 got {len(data2)} rows")

            total_rows += code_rows
            success_codes += 1

            if (i + 1) % 5 == 0:
                logger.info(f"fx_daily: {i+1}/{len(codes)} codes done ({total_rows} rows so far)")

            time.sleep(0.1)

        except Exception as e:
            fail_codes += 1
            logger.error(f"fx_daily {ts_code} failed: {e}")

    # Update checkpoint
    cursor = db_conn.cursor()
    cursor.execute("SELECT MIN(trade_date), MAX(trade_date) FROM fx_daily")
    row = cursor.fetchone()
    first_date = row[0] if row else None
    last_date = row[1] if row else None

    cursor.execute(
        "INSERT INTO sync_checkpoint (table_name, last_sync_date, updated_at) "
        "VALUES ('fx_daily', %s, NOW()) "
        "ON CONFLICT (table_name) DO UPDATE SET last_sync_date = %s, updated_at = NOW()",
        (date.today(), date.today())
    )
    db_conn.commit()
    cursor.close()

    logger.info(f"fx_daily: completed {total_rows} rows, {success_codes} codes OK, {fail_codes} failed, range: {first_date}~{last_date}")

    return {
        "total_rows": total_rows,
        "codes": success_codes,
        "failed_codes": fail_codes,
        "first_date": first_date,
        "last_date": last_date,
    }


def _write_upsert(db_conn, table_name: str, df: pd.DataFrame, pk_cols: list):
    """Write dataframe to table using upsert via temp table"""
    from data.database.connection import engine as sa_engine
    temp_table = f"_tmp_{table_name}"
    df.to_sql(temp_table, sa_engine, if_exists="replace", index=False, method="multi")

    # Get column types
    cursor = db_conn.cursor()
    cursor.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = %s ORDER BY ordinal_position",
        (table_name,)
    )
    col_types = {row[0]: row[1] for row in cursor.fetchall()}

    all_cols = [c for c in df.columns if c in col_types]
    if not all_cols:
        cursor.execute(f"DROP TABLE IF EXISTS {temp_table}")
        db_conn.commit()
        cursor.close()
        return

    select_cols = []
    for c in all_cols:
        pg_type = col_types[c]
        if pg_type in ("double precision", "numeric", "real", "integer", "bigint", "smallint"):
            select_cols.append(f"CAST({c} AS {pg_type})")
        else:
            select_cols.append(c)

    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in all_cols if c not in pk_cols)
    distinct_cols = ", ".join(pk_cols)
    select_expr = ", ".join(select_cols)

    sql = f"""
        INSERT INTO {table_name} ({', '.join(all_cols)})
        SELECT {select_expr} FROM {temp_table}
        ON CONFLICT ({distinct_cols}) DO UPDATE SET {set_clause}
    """
    cursor.execute(sql)
    cursor.execute(f"DROP TABLE IF EXISTS {temp_table}")
    db_conn.commit()
    cursor.close()
