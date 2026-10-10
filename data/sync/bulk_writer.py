# -*- coding: utf-8 -*-
"""COPY 化批量写入（MINUTE_DATA_PLAN.md 5.3）。

替代原 engine._write_df 的 to_sql 逐行协议：
每批: TRUNCATE 临时表 -> COPY csv FROM STDIN -> INSERT..SELECT(去重+CAST) ON CONFLICT DO UPDATE
列类型按表名进程级缓存，避免每批复查 information_schema。

日线同步（engine._write_df 委托）与分钟同步（minute_sync）共用本模块。
SQL 一律参数化或经白名单列名拼接（列名来自 information_schema 与 cfg.fields，非用户输入）。
"""
import io
import pandas as pd
from sqlalchemy import text
from data.database.connection import engine
from utils.logger import get_logger

logger = get_logger("bulk_writer")

# 进程级列类型缓存: {table_name: {column_name: data_type}}
_COL_TYPES_CACHE = {}

_ORDER_PRIORITY = ("f_ann_date", "ann_date", "report_type")


def get_col_types(table_name: str) -> dict:
    """查目标表列类型（进程级缓存）。"""
    if table_name in _COL_TYPES_CACHE:
        return _COL_TYPES_CACHE[table_name]
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :tbl ORDER BY ordinal_position"
            ),
            {"tbl": table_name},
        ).fetchall()
    col_types = {r[0]: r[1] for r in rows}
    _COL_TYPES_CACHE[table_name] = col_types
    return col_types


def _cast_expr(col: str, pg_type: str) -> str:
    if pg_type in ("double precision", "numeric", "real", "integer", "bigint", "smallint",
                   "date", "timestamp without time zone", "timestamp with time zone"):
        return f"CAST({col} AS {pg_type})"
    return col


def copy_upsert_df(table_name: str, df: pd.DataFrame, pk_cols=None, batch_rows: int = 50000):
    """DataFrame -> 临时表 COPY -> 去重 upsert 进目标表。

    pk_cols: 冲突键列名列表；None 时纯 INSERT（无 upsert）。
    返回写入行数。
    """
    if df is None or df.empty:
        return 0

    col_types = get_col_types(table_name)
    all_cols = [c for c in df.columns if c in col_types]
    if not all_cols:
        logger.warning(f"{table_name}: no matching columns, skip write")
        return 0

    pk_cols = [c for c in (pk_cols or []) if c in all_cols]
    temp_table = f"_tmp_{table_name}"

    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cols_def = ", ".join(f"{c} TEXT" for c in all_cols)
        cur.execute(f"CREATE TEMP TABLE IF NOT EXISTS {temp_table} ({cols_def})")

        select_cols = ", ".join(_cast_expr(c, col_types[c]) for c in all_cols)

        if pk_cols:
            distinct_cols = ", ".join(pk_cols)
            order_cols = [c for c in _ORDER_PRIORITY if c in all_cols]
            order_by = ", ".join(order_cols + [distinct_cols])
            select_from = (
                f"(SELECT * FROM (SELECT *, ROW_NUMBER() OVER "
                f"(PARTITION BY {distinct_cols} ORDER BY {order_by}) AS _rn "
                f"FROM {temp_table}) _ranked WHERE _rn = 1) _dedup"
            )
            set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in all_cols if c not in pk_cols)
        else:
            select_from = temp_table
            set_clause = ""

        total = 0
        for start in range(0, len(df), batch_rows):
            batch = df.iloc[start:start + batch_rows]
            cur.execute(f"TRUNCATE TABLE {temp_table}")

            buf = io.StringIO()
            batch[all_cols].to_csv(buf, index=False, header=False, lineterminator="\n")
            buf.seek(0)
            cur.copy_expert(
                f"COPY {temp_table} ({', '.join(all_cols)}) FROM STDIN WITH (FORMAT csv)",
                buf,
            )

            insert_sql = (
                f"INSERT INTO {table_name} ({', '.join(all_cols)}) "
                f"SELECT {select_cols} FROM {select_from}"
            )
            if pk_cols and set_clause:
                insert_sql += f" ON CONFLICT ({', '.join(pk_cols)}) DO UPDATE SET {set_clause}"
            cur.execute(insert_sql)
            total += len(batch)

        raw_conn.commit()
        return total
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()
