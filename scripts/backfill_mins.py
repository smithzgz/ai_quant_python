# -*- coding: utf-8 -*-
"""5分钟线历史回填器（MINUTE_DATA_PLAN.md V1.2 §5.4 / M3）。

单线程（baostock 全局单连接不可多线程）、断点续传、advisory lock 互斥。

用法:
    python scripts/backfill_mins.py --codes 000001.SZ,600519.SH --start 2023-01-01 --end 2026-09-30
    python scripts/backfill_mins.py --universe hs300 --start 2023-01-01
    python scripts/backfill_mins.py --status              # 查看回填进度
    python scripts/backfill_mins.py --finalize            # 回填完成后：挂压缩策略 + 刷新 cagg + 挂 cagg 策略

回填期间不挂任何策略（V1.1 纪律：避免已压缩 chunk 反复解压/重压）；
全部回填完成后执行 --finalize 一次性收尾。
"""
import argparse
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from data.database.connection import engine
from config.settings import settings
from utils.logger import get_logger

logger = get_logger("backfill_mins")

TABLE = "stk_mins_5min"


def get_universe(name: str) -> list:
    """指数成分股（index_weight 取最新一期成分）。"""
    name_map = {"hs300": "000300.SH", "zz500": "000905.SH", "sz50": "000016.SH"}
    index_code = name_map.get(name.lower())
    if not index_code:
        raise SystemExit(f"unknown universe: {name} (可选: {list(name_map)})")
    with engine.connect() as conn:
        latest = conn.execute(text(
            "SELECT MAX(trade_date) FROM index_weight WHERE index_code = :i"
        ), {"i": index_code}).scalar()
        if latest is None:
            raise SystemExit(f"index_weight has no data for {index_code}")
        rows = conn.execute(text(
            "SELECT DISTINCT con_code FROM index_weight "
            "WHERE index_code = :i AND trade_date = :d ORDER BY con_code"
        ), {"i": index_code, "d": latest}).fetchall()
    return [r[0] for r in rows]


def cmd_backfill(codes: list, start: str, end: str):
    from datetime import date as _date
    from data.sync.minute_sync import sync_codes

    start_d = _date.fromisoformat(start)
    end_d = _date.fromisoformat(end) if end else _date.today()
    checkpoints = _existing_checkpoints(codes)
    todo = [c for c in codes if checkpoints.get(c, _date.min) < end_d]
    print(f"backfill: {len(todo)}/{len(codes)} codes need work "
          f"({start_d} ~ {end_d}), est. {len(todo) * 0.003 * ((end_d - start_d).days / 365 + 1):.0f} min")

    t0 = time.time()
    result = sync_codes(todo, start_d, end_d)
    print(f"done: {result}, elapsed {time.time() - t0:.0f}s")


def _existing_checkpoints(codes: list) -> dict:
    from datetime import date as _date
    from data.sync.minute_sync import _get_checkpoints
    cps = _get_checkpoints(codes)
    return {c: v.date() for c, v in cps.items()}


def cmd_status(codes: list = None):
    """按年统计每股已入库 bar 数与 checkpoint 进度。"""
    where = "WHERE ts_code = ANY(:codes)" if codes else ""
    params = {"codes": codes} if codes else {}
    with engine.connect() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE} {where}"), params).scalar()
        n_codes = conn.execute(
            text(f"SELECT COUNT(DISTINCT ts_code) FROM {TABLE} {where}"), params).scalar()
        print(f"table {TABLE}: {total:,} rows / {n_codes} codes")
        print("\nrows per year (top/bottom 5):")
        yearly = conn.execute(text(
            f"SELECT ts_code, EXTRACT(YEAR FROM trade_time)::int AS y, COUNT(*) AS n "
            f"FROM {TABLE} {where} GROUP BY 1, 2 ORDER BY n DESC LIMIT 5"
        ), params).fetchall()
        for r in yearly:
            print(f"  {r[0]} {r[1]}: {r[2]:,}")
        print("\ncheckpoint coverage:")
        cp = conn.execute(text(
            "SELECT COUNT(*), MIN(last_sync_time)::date, MAX(last_sync_time)::date "
            "FROM sync_code_checkpoint WHERE table_name = :t"
        ), {"t": TABLE}).fetchone()
        print(f"  {cp[0]} codes checkpointed, range {cp[1]} ~ {cp[2]}")
        if codes:
            missing = set(codes) - {r[0] for r in conn.execute(text(
                "SELECT DISTINCT ts_code FROM " + TABLE + " WHERE ts_code = ANY(:codes)"), params).fetchall()}
            if missing:
                print(f"  codes with no data: {sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}")


def cmd_finalize():
    """回填收尾（幂等）：逐块压缩 -> 挂压缩策略 -> 按月刷新 cagg -> 挂 cagg 策略。

    执行前置条件：回填已全部完成（--status 确认），且夜间增量任务暂停
    （策略挂载期间 19:00 任务写入会触发解压——建议挂载时段避开 19:00）。
    """
    print("=== finalize: compression policy + cagg refresh ===")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # 1. 逐块压缩 stk_mins_5min
        chunks = conn.execute(text("SELECT show_chunks(:t)"), {"t": TABLE}).scalars().all()
        print(f"compressing {len(chunks)} chunks of {TABLE}...")
        for i, ch in enumerate(chunks, 1):
            conn.execute(text("SELECT compress_chunk(:c)"), {"c": ch})
            if i % 20 == 0:
                print(f"  {i}/{len(chunks)}")
        conn.execute(text(
            f"SELECT add_compression_policy('{TABLE}', INTERVAL '30 days')"
        ))
        print("compression policy attached (30 days)")

        # 2. 按月分批刷新 cagg（水位线不覆盖历史区间）
        (lo, hi) = conn.execute(text(
            f"SELECT MIN(trade_time)::date, MAX(trade_time)::date FROM {TABLE}"
        )).fetchone()
        if lo is None:
            raise SystemExit("table empty, nothing to refresh")
        print(f"refreshing stk_mins_1day month by month: {lo} ~ {hi}")
        cur = lo.replace(day=1)
        from datetime import date as _date
        while cur <= hi:
            nxt = (_date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1))
            conn.execute(text(
                "CALL refresh_continuous_aggregate('stk_mins_1day', :a, :b)"
            ), {"a": cur, "b": nxt})
            print(f"  {cur} ~ {nxt} refreshed")
            cur = nxt
        conn.execute(text(
            "SELECT add_continuous_aggregate_policy('stk_mins_1day', "
            "start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour', "
            "schedule_interval => INTERVAL '1 hour')"
        ))
        print("cagg refresh policy attached")

    print("=== finalize done ===")


def main():
    ap = argparse.ArgumentParser(description="5分钟线历史回填器")
    ap.add_argument("--codes", help="逗号分隔 ts_code 列表")
    ap.add_argument("--universe", help="指数成分股: hs300 | zz500 | sz50")
    ap.add_argument("--start", help="开始日期 YYYY-MM-DD")
    ap.add_argument("--end", help="结束日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--status", action="store_true", help="查看回填进度")
    ap.add_argument("--finalize", action="store_true", help="回填收尾：压缩+挂策略+刷cagg")
    args = ap.parse_args()

    if args.finalize:
        cmd_finalize()
        return
    if args.status:
        codes = args.codes.split(",") if args.codes else None
        cmd_status(codes)
        return

    if args.universe:
        codes = get_universe(args.universe)
    elif args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        raise SystemExit("必须指定 --codes 或 --universe（或 --status/--finalize）")
    if not args.start:
        raise SystemExit("回填必须指定 --start YYYY-MM-DD")

    print(f"universe: {len(codes)} codes, e.g. {codes[:5]}")
    cmd_backfill(codes, args.start, args.end)


if __name__ == "__main__":
    main()
