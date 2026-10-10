# -*- coding: utf-8 -*-
"""分钟级数据质检规则（MINUTE_DATA_PLAN.md V1.2 §6.3）。

全部 SQL 聚合下推：DB 侧聚合返回异常摘要，禁止把全表拉到 pandas。
规则针对 stk_mins_5min（48 bar/交易日：09:30~11:30 / 13:00~15:00 各 24 根）。

注意：stk_mins_5min.vol 单位为股，与 daily.vol（手）口径差 100 倍，不得跨表直接对比。
"""
from sqlalchemy import text
from data.database.connection import engine
from utils.logger import get_logger

logger = get_logger("minute_quality")

TABLE = "stk_mins_5min"


def check_bars_per_day(conn, start=None, end=None, low=46, high=50, sample=10):
    """每个交易日 bar 数 = 48±2（集合竞价缺失等容差）。返回异常 (ts_code, date, cnt) 摘要。"""
    where, params = "", {"low": low, "high": high, "limit": sample}
    if start:
        where += " AND trade_time >= :s_start"
        params["s_start"] = start
    if end:
        where += " AND trade_time < :s_end"
        params["s_end"] = end
    rows = conn.execute(text(
        f"SELECT ts_code, trade_time::date AS d, COUNT(*) AS cnt "
        f"FROM {TABLE} WHERE 1=1 {where} "
        f"GROUP BY 1, 2 HAVING COUNT(*) NOT BETWEEN :low AND :high "
        f"ORDER BY cnt LIMIT :limit"
    ), params).fetchall()
    return rows


def check_price_logic(conn, start=None, end=None, sample=10):
    """OHLC 逻辑一致性（DB 侧过滤，只回异常行数与样例）。"""
    params = {"limit": sample}
    where = ""
    if start:
        where += " AND trade_time >= :p_start"
        params["p_start"] = start
    if end:
        where += " AND trade_time < :p_end"
        params["p_end"] = end
    total = conn.execute(text(
        f"SELECT COUNT(*) FROM {TABLE} WHERE 1=1 {where} AND ("
        f"open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL "
        f"OR high < low OR high < open OR high < close "
        f"OR low > open OR low > close)"
    ), params).scalar()
    sample_rows = conn.execute(text(
        f"SELECT ts_code, trade_time, open, high, low, close FROM {TABLE} "
        f"WHERE 1=1 {where} AND (high < low OR high < open OR high < close OR low > open OR low > close) "
        f"LIMIT :limit"
    ), params).fetchall()
    return {"count": total, "sample": sample_rows}


def check_time_gaps(conn, start=None, end=None, sample=10):
    """日内时间缺口：5min 序列中断且非午休/收盘边界。

    正常边界: 11:25 -> 13:00（午休），14:55 -> 次日 09:30（收盘）。
    """
    params = {"limit": sample}
    where = ""
    if start:
        where += " AND trade_time >= :g_start"
        params["g_start"] = start
    if end:
        where += " AND trade_time < :g_end"
        params["g_end"] = end
    rows = conn.execute(text(
        f"WITH seq AS ("
        f"  SELECT ts_code, trade_time, "
        f"         LAG(trade_time) OVER (PARTITION BY ts_code, trade_time::date ORDER BY trade_time) AS prev_t "
        f"  FROM {TABLE} WHERE 1=1 {where}"
        f")"
        f"SELECT ts_code, prev_t, trade_time, EXTRACT(EPOCH FROM (trade_time - prev_t))/60 AS gap_min "
        f"FROM seq WHERE prev_t IS NOT NULL AND (trade_time - prev_t) > INTERVAL '5 minutes' "
        f"AND NOT (EXTRACT(HOUR FROM prev_t) = 11 AND EXTRACT(MINUTE FROM prev_t) = 25) "
        f"ORDER BY gap_min DESC LIMIT :limit"
    ), params).fetchall()
    return rows


def check_suspicious_zero_volume(conn, start=None, end=None, sample=10):
    """零成交且 OHLC 不相等（可疑脏数据；vol=0 且 OHLC 全等为正常无成交分钟）。"""
    params = {"limit": sample}
    where = ""
    if start:
        where += " AND trade_time >= :z_start"
        params["z_start"] = start
    if end:
        where += " AND trade_time < :z_end"
        params["z_end"] = end
    total = conn.execute(text(
        f"SELECT COUNT(*) FROM {TABLE} WHERE 1=1 {where} "
        f"AND vol = 0 AND (open != close OR high != low OR high != open)"
    ), params).scalar()
    return total


def run_minute_quality_checks(start=None, end=None) -> dict:
    """执行全部分钟质检规则，返回汇总（异常量 + 样例）。"""
    result = {}
    with engine.connect() as conn:
        result["bars_per_day"] = [list(map(str, r)) for r in check_bars_per_day(conn, start, end)]
        price = check_price_logic(conn, start, end)
        result["price_logic"] = {"count": price["count"],
                                 "sample": [list(map(str, r)) for r in price["sample"]]}
        result["time_gaps"] = [list(map(str, r)) for r in check_time_gaps(conn, start, end)]
        result["suspicious_zero_volume"] = check_suspicious_zero_volume(conn, start, end)
    ok = (not result["bars_per_day"] and result["price_logic"]["count"] == 0
          and not result["time_gaps"] and result["suspicious_zero_volume"] == 0)
    result["status"] = "pass" if ok else "issues_found"
    logger.info(f"minute quality: {result['status']}")
    return result
