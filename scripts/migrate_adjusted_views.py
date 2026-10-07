# -*- coding: utf-8 -*-
"""
迁移：daily_qfq / daily_hfq 物化表 -> 视图

背景（旧实现的两个正确性缺陷）:
1. 前复权因子基准用的是全局 MAX(trade_date)，停牌/退市股在该日无 adj_factor 记录，
   JOIN 失败导致这些股票的 qfq 数据整段丢失；
2. 增量更新只覆盖 synced 日期范围，而除权事件发生后前复权全历史都需要重算，
   导致历史 qfq 数据陈旧。

视图方案:
- 每只股票取自己的最新因子（DISTINCT ON ts_code ORDER BY trade_date DESC）
- 查询时实时计算，无增量维护，永不陈旧
- 依赖 daily/adj_factor 的主键索引，单股票查询走谓词下推，Grafana 查询性能可接受
- 省去 2 x 1780 万行冗余存储

用法:
    python scripts/migrate_adjusted_views.py           # 执行迁移
    python scripts/migrate_adjusted_views.py --verify  # 仅验证视图正确性
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from data.database.connection import engine

QFQ_VIEW = """
CREATE OR REPLACE VIEW daily_qfq AS
WITH latest_factor AS (
    SELECT DISTINCT ON (ts_code) ts_code, adj_factor
    FROM adj_factor
    ORDER BY ts_code, trade_date DESC
)
SELECT
    d.ts_code,
    d.trade_date,
    ROUND((d.open * af.adj_factor / lf.adj_factor)::numeric, 4) AS open,
    ROUND((d.high * af.adj_factor / lf.adj_factor)::numeric, 4) AS high,
    ROUND((d.low * af.adj_factor / lf.adj_factor)::numeric, 4) AS low,
    ROUND((d.close * af.adj_factor / lf.adj_factor)::numeric, 4) AS close,
    ROUND((d.pre_close * af.adj_factor / lf.adj_factor)::numeric, 4) AS pre_close,
    ROUND(((d.close - d.pre_close) * af.adj_factor / lf.adj_factor)::numeric, 4) AS change,
    d.pct_chg,
    d.vol,
    d.amount,
    af.adj_factor
FROM daily d
JOIN adj_factor af ON af.ts_code = d.ts_code AND af.trade_date = d.trade_date
JOIN latest_factor lf ON lf.ts_code = d.ts_code
"""

HFQ_VIEW = """
CREATE OR REPLACE VIEW daily_hfq AS
SELECT
    d.ts_code,
    d.trade_date,
    ROUND((d.open * af.adj_factor)::numeric, 4) AS open,
    ROUND((d.high * af.adj_factor)::numeric, 4) AS high,
    ROUND((d.low * af.adj_factor)::numeric, 4) AS low,
    ROUND((d.close * af.adj_factor)::numeric, 4) AS close,
    ROUND((d.pre_close * af.adj_factor)::numeric, 4) AS pre_close,
    ROUND(((d.close - d.pre_close) * af.adj_factor)::numeric, 4) AS change,
    d.pct_chg,
    d.vol,
    d.amount,
    af.adj_factor
FROM daily d
JOIN adj_factor af ON af.ts_code = d.ts_code AND af.trade_date = d.trade_date
"""


def is_table(conn, name: str) -> bool:
    return conn.execute(
        text("SELECT COUNT(*) FROM pg_tables WHERE tablename = :n"), {"n": name}
    ).scalar() > 0


def is_view(conn, name: str) -> bool:
    return conn.execute(
        text("SELECT COUNT(*) FROM pg_views WHERE viewname = :n"), {"n": name}
    ).scalar() > 0


def migrate():
    with engine.begin() as conn:
        for name in ("daily_qfq", "daily_hfq"):
            if is_table(conn, name):
                conn.execute(text(f"DROP TABLE {name}"))
                print(f"Dropped table {name} (数据可由 daily + adj_factor 实时推导)")
            elif is_view(conn, name):
                print(f"{name} already a view, recreating")
            else:
                print(f"{name} does not exist, creating view")
        conn.execute(text(QFQ_VIEW))
        conn.execute(text(HFQ_VIEW))
        print("Created views daily_qfq / daily_hfq")


def verify():
    with engine.connect() as conn:
        for name in ("daily_qfq", "daily_hfq"):
            if not is_view(conn, name):
                print(f"FAIL: {name} is not a view")
                return False
            n = conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar()
            print(f"{name}: view OK, {n:,} rows")

        row = conn.execute(text("""
            SELECT q.ts_code, q.trade_date, q.close AS qfq_close, d.close AS raw_close,
                   af.adj_factor, lf.latest_factor
            FROM daily_qfq q
            JOIN daily d ON d.ts_code = q.ts_code AND d.trade_date = q.trade_date
            JOIN adj_factor af ON af.ts_code = q.ts_code AND af.trade_date = q.trade_date
            CROSS JOIN LATERAL (
                SELECT adj_factor AS latest_factor FROM adj_factor a2
                WHERE a2.ts_code = q.ts_code ORDER BY trade_date DESC LIMIT 1
            ) lf
            WHERE d.vol > 0
            ORDER BY q.trade_date DESC, q.ts_code
            LIMIT 3
        """)).fetchall()
        for r in row:
            expected = round(float(r[2]), 4)
            calc = round(float(r[3]) * float(r[4]) / float(r[5]), 4)
            status = "OK" if abs(expected - calc) < 0.001 else "MISMATCH"
            print(f"  {r[0]} {r[1]}: qfq={expected} recalc={calc} [{status}]")

        latest = conn.execute(text("""
            SELECT COUNT(*) FROM daily_qfq q
            JOIN (SELECT DISTINCT ON (ts_code) ts_code, trade_date
                  FROM adj_factor ORDER BY ts_code, trade_date DESC) latest
              ON latest.ts_code = q.ts_code AND latest.trade_date = q.trade_date
        """)).scalar()
        print(f"rows at per-stock latest factor date: {latest} (应大于0)")
        return True


if __name__ == "__main__":
    if "--verify" in sys.argv:
        verify()
    else:
        migrate()
        verify()
