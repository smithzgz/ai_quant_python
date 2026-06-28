# -*- coding: utf-8 -*-
"""
Bond (可转债) sync utilities.
bond_basic uses standard Tushare API (engine handles sync).
This module provides validate_coverage for admin API.
"""
from utils.logger import get_logger

logger = get_logger("bond_sync")


def validate_coverage(cursor) -> dict:
    """Validate bond_basic data coverage. Returns gap report."""
    cursor.execute("""
        SELECT exchange, COUNT(*) AS cnt
        FROM bond_basic
        GROUP BY exchange
        ORDER BY exchange
    """)
    exchange_rows = cursor.fetchall()

    cursor.execute("""
        SELECT SUBSTRING(list_date, 1, 4)::int AS yr, COUNT(*) AS cnt
        FROM bond_basic
        WHERE list_date IS NOT NULL
        GROUP BY yr ORDER BY yr
    """)
    yearly_rows = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) FROM bond_basic")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT stk_code) FROM bond_basic WHERE stk_code IS NOT NULL")
    total_stocks = cursor.fetchone()[0]

    cursor.execute("SELECT MIN(list_date), MAX(list_date) FROM bond_basic")
    min_date, max_date = cursor.fetchone()

    return {
        'total': total,
        'total_stocks': total_stocks,
        'min_date': min_date,
        'max_date': max_date,
        'by_exchange': {row[0]: row[1] for row in exchange_rows},
        'by_year': {row[0]: row[1] for row in yearly_rows},
    }
