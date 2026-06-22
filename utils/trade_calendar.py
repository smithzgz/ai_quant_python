# -*- coding: utf-8 -*-
from data.database.connection import engine
from utils.logger import get_logger

logger = get_logger("trade_calendar")


def get_trade_dates(start_date=None, end_date=None) -> list:
    from sqlalchemy import text
    sql = "SELECT cal_date FROM trade_cal WHERE is_open = 1"
    params = {}
    if start_date:
        sql += " AND cal_date >= :start"
        params["start"] = start_date
    if end_date:
        sql += " AND cal_date <= :end"
        params["end"] = end_date
    sql += " ORDER BY cal_date"

    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        return [row[0] for row in result.fetchall()]


def is_trade_date(d) -> bool:
    from sqlalchemy import text
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT is_open FROM trade_cal WHERE cal_date = :d"),
            {"d": d},
        ).scalar()
        return result == 1
