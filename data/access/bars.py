# -*- coding: utf-8 -*-
"""统一分钟/日线数据访问层（MINUTE_DATA_PLAN.md V1.2 §6）。

路由:
- 1d    -> daily 表（qfq 查询时 join adj_factor，不物化）
- 5min  -> stk_mins_5min 基础表（COPY csv 批量通道，亿行级读取瓶颈规避）
- 15min/60min -> 5min 查询时 time_bucket 上卷（无独立存储）

保护:
- 5min 必须 max_symbols（默认 500）防止误用全市场扫描；
- qfq 基准 = 每股最新 adj_factor（与 daily_qfq 视图同一口径）。
"""
import io
from datetime import date, datetime

import pandas as pd
from psycopg2 import sql as pgsql
from sqlalchemy import text
from data.database.connection import engine
from utils.logger import get_logger

logger = get_logger("bars")

OHLCV = ["open", "high", "low", "close", "vol", "amount"]
MAX_SYMBOLS_5MIN = 500

# 单股最新复权因子（与 daily_qfq 视图口径一致）
_LATEST_FACTOR_SQL = (
    "SELECT DISTINCT ON (ts_code) ts_code, adj_factor FROM adj_factor "
    "ORDER BY ts_code, trade_date DESC"
)


def _guard_symbols(symbols, max_symbols):
    symbols = list(dict.fromkeys(symbols))
    if max_symbols is not None and len(symbols) > max_symbols:
        raise ValueError(
            f"{len(symbols)} symbols exceeds max_symbols={max_symbols} "
            f"(防止全市场扫描，分批调用)"
        )
    return symbols


def _copy_query(query: pgsql.Composable) -> pd.DataFrame:
    """psycopg2 COPY TO STDOUT -> DataFrame（sql.SQL 组合防注入）。"""
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        buf = io.StringIO()
        cur.copy_expert(query, buf)
        buf.seek(0)
        return pd.read_csv(buf)
    finally:
        raw.close()


def _load_5min(symbols, start, end, columns, adjust) -> pd.DataFrame:
    cols = [c for c in (["ts_code", "trade_time"] + list(columns)) if c != "ts_code" and c != "trade_time"]
    col_list = ", ".join(f"b.{c}" for c in cols)

    if adjust == "qfq":
        select = (
            "b.ts_code, b.trade_time, "
            "b.open * af.adj_factor / lf.adj_factor AS open, "
            "b.high * af.adj_factor / lf.adj_factor AS high, "
            "b.low * af.adj_factor / lf.adj_factor AS low, "
            "b.close * af.adj_factor / lf.adj_factor AS close, "
            "b.vol, b.amount"
        )
        joins = (
            " JOIN daily d ON d.ts_code = b.ts_code AND d.trade_date = b.trade_time::date"
            " JOIN adj_factor af ON af.ts_code = b.ts_code AND af.trade_date = b.trade_time::date"
            f" JOIN ({_LATEST_FACTOR_SQL}) lf ON lf.ts_code = b.ts_code"
        )
    else:
        select = ", ".join(["b.ts_code", "b.trade_time"] + cols)
        joins = ""

    query = pgsql.SQL(
        "COPY (SELECT {select} FROM stk_mins_5min b{joins} "
        "WHERE b.ts_code = ANY({codes}) AND b.trade_time >= {start} AND b.trade_time <= {end} "
        "ORDER BY b.ts_code, b.trade_time) TO STDOUT WITH (FORMAT csv, HEADER TRUE)"
    ).format(
        select=pgsql.SQL(select),
        joins=pgsql.SQL(joins),
        codes=pgsql.Literal(symbols),
        start=pgsql.Literal(str(start)),
        end=pgsql.Literal(str(end)),
    )
    df = _copy_query(query)
    if df.empty:
        return df
    df["trade_time"] = pd.to_datetime(df["trade_time"], utc=True).dt.tz_convert("Asia/Shanghai")
    return df


def _load_15m_60m(symbols, start, end, columns, adjust, freq) -> pd.DataFrame:
    """5min 查询时上卷到 15/60min（不建 cagg）。

    A股 60min K 线惯例：早盘 09:30-11:30 两根、午后 13:00-15:00 两根。
    每根 bar 直接映射到所属桶（session 锚点 09:30/13:00 + floor），再 groupby 聚合。
    """
    n_minutes = int(freq[:-3])   # "15min" -> 15
    if adjust == "qfq":
        # 先在 5min 粒度做 qfq 再上卷（先聚合后复权对 OHLC 不等价）
        base = _load_5min(symbols, start, end, OHLCV, adjust)
    else:
        base = _load_5min(symbols, start, end, OHLCV, "none")
    if base.empty:
        return base

    td = pd.Timedelta(minutes=n_minutes)

    def _bucket(ts):
        anchor = ts.replace(hour=9, minute=30, second=0) if ts.hour < 12 \
            else ts.replace(hour=13, minute=0, second=0)
        return anchor + ((ts - anchor) // td) * td

    base = base.set_index("trade_time")
    base["bucket"] = base.index.map(_bucket)
    agg = base.groupby(["ts_code", "bucket"]).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        vol=("vol", "sum"), amount=("amount", "sum"),
    ).reset_index().rename(columns={"bucket": "trade_time"})
    keep = ["ts_code", "trade_time"] + [c for c in columns if c not in ("ts_code", "trade_time")]
    return agg[keep]


def _load_1d(symbols, start, end, columns, adjust) -> pd.DataFrame:
    if adjust == "qfq":
        query = pgsql.SQL(
            "COPY (SELECT d.ts_code, d.trade_date AS trade_time, "
            "d.open * af.adj_factor / lf.adj_factor AS open, "
            "d.high * af.adj_factor / lf.adj_factor AS high, "
            "d.low * af.adj_factor / lf.adj_factor AS low, "
            "d.close * af.adj_factor / lf.adj_factor AS close, "
            "d.vol, d.amount "
            "FROM daily d "
            "JOIN adj_factor af ON af.ts_code = d.ts_code AND af.trade_date = d.trade_date "
            f"JOIN ({_LATEST_FACTOR_SQL}) lf ON lf.ts_code = d.ts_code "
            "WHERE d.ts_code = ANY({codes}) AND d.trade_date >= {start} AND d.trade_date <= {end} "
            "ORDER BY d.ts_code, d.trade_date) TO STDOUT WITH (FORMAT csv, HEADER TRUE)"
        ).format(
            codes=pgsql.Literal(symbols),
            start=pgsql.Literal(str(start)),
            end=pgsql.Literal(str(end)),
        )
    else:
        query = pgsql.SQL(
            "COPY (SELECT d.ts_code, d.trade_date AS trade_time, d.open, d.high, d.low, d.close, "
            "d.vol, d.amount FROM daily d "
            "WHERE d.ts_code = ANY({codes}) AND d.trade_date >= {start} AND d.trade_date <= {end} "
            "ORDER BY d.ts_code, d.trade_date) TO STDOUT WITH (FORMAT csv, HEADER TRUE)"
        ).format(
            codes=pgsql.Literal(symbols),
            start=pgsql.Literal(str(start)),
            end=pgsql.Literal(str(end)),
        )
    df = _copy_query(query)
    if not df.empty:
        df["trade_time"] = pd.to_datetime(df["trade_time"])
    return df


def load_bars(
    symbols: list,
    start,
    end,
    freq: str = "1d",
    columns: list = None,
    adjust: str = "none",
    max_symbols: int = None,
) -> dict:
    """加载多股票 OHLCV，返回 {ts_code: DataFrame(trade_time 索引)}。

    - freq: "1d" | "5min" | "15min" | "60min"
    - adjust: "none" | "qfq"（查询时计算，复权不物化）
    - 5min 及以上频率须传 max_symbols（默认 500）防全市场扫描
    """
    symbols = _guard_symbols(symbols, max_symbols if max_symbols is not None
                             else (MAX_SYMBOLS_5MIN if freq != "1d" else None))
    columns = list(columns) if columns else list(OHLCV)
    if not symbols:
        return {}

    if freq == "5min":
        df = _load_5min(symbols, start, end, columns, adjust)
    elif freq in ("15min", "60min"):
        df = _load_15m_60m(symbols, start, end, columns, adjust, freq)
    elif freq == "1d":
        df = _load_1d(symbols, start, end, columns, adjust)
    else:
        raise ValueError(f"unsupported freq: {freq}")

    if df.empty:
        return {s: pd.DataFrame(columns=["trade_time"] + columns).set_index("trade_time") for s in symbols}

    out = {}
    for code, g in df.groupby("ts_code"):
        g = g.drop(columns=["ts_code"]).set_index("trade_time").sort_index()
        out[code] = g
    for s in symbols:
        out.setdefault(s, pd.DataFrame(columns=columns).set_index(
            pd.DatetimeIndex([], name="trade_time", tz="UTC" if freq != "1d" else None)))
    return out
