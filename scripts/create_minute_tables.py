# -*- coding: utf-8 -*-
"""
M1 存储就绪：分钟数据三件套建表（幂等，可重复执行）

按 MINUTE_DATA_PLAN.md V1.2 第 4 节：
- stk_mins_5min   : 5分钟K线基础表（hypertable, 7d chunk, 压缩仅声明配置）
- stk_mins_1day   : 日级连续聚合（WITH NO DATA）
- sync_code_checkpoint : 按股票断点表（普通表，ORM 同步定义）

V1.1 关键纪律：建表期【不挂】任何压缩/刷新/保留策略——
回填完成后由 scripts/backfill_mins.py --finalize 统一手动压缩并挂策略。

用法:
    python scripts/create_minute_tables.py           # 执行建表
    python scripts/create_minute_tables.py --verify  # 仅自校验
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from data.database.connection import engine
from utils.logger import get_logger

logger = get_logger("create_minute_tables")

CREATE_STK_MINS = """
CREATE TABLE IF NOT EXISTS stk_mins_5min (
    ts_code     VARCHAR(20)  NOT NULL,
    trade_time  TIMESTAMPTZ  NOT NULL,
    open        DOUBLE PRECISION,
    high        DOUBLE PRECISION,
    low         DOUBLE PRECISION,
    close       DOUBLE PRECISION,
    vol         DOUBLE PRECISION,
    amount      DOUBLE PRECISION,
    PRIMARY KEY (ts_code, trade_time)
)
"""

CREATE_CHECKPOINT = """
CREATE TABLE IF NOT EXISTS sync_code_checkpoint (
    table_name      VARCHAR(100) NOT NULL,
    ts_code         VARCHAR(20)  NOT NULL,
    last_sync_time  TIMESTAMPTZ  NOT NULL,
    updated_at      TIMESTAMPTZ  DEFAULT NOW(),
    PRIMARY KEY (table_name, ts_code)
)
"""

# cagg: 从 5min 基础表聚合日级（供分钟衍生分析；日频口径以 daily 为准）
CREATE_CAGG_1DAY = """
CREATE MATERIALIZED VIEW stk_mins_1day
WITH (timescaledb.continuous) AS
SELECT ts_code,
       time_bucket('1 day', trade_time) AS bucket,
       first(open, trade_time)  AS open,
       max(high)                AS high,
       min(low)                 AS low,
       last(close, trade_time)  AS close,
       sum(vol)                 AS vol,
       sum(amount)              AS amount
FROM stk_mins_5min
GROUP BY ts_code, bucket
WITH NO DATA
"""

COMPRESS_CONFIG = (
    "ALTER TABLE {table} SET (timescaledb.compress, "
    "timescaledb.compress_segmentby = 'ts_code', "
    "timescaledb.compress_orderby = '{col} DESC')"
)

# cagg 需用 ALTER MATERIALIZED VIEW（Timescale 2.13+ 不再暴露 cagg 内部 hypertable）
COMPRESS_CONFIG_CAGG = (
    "ALTER MATERIALIZED VIEW stk_mins_1day SET (timescaledb.compress, "
    "timescaledb.compress_segmentby = 'ts_code')"
)


def _is_hypertable(conn, table: str) -> bool:
    return conn.execute(
        text("SELECT COUNT(*) FROM timescaledb_information.hypertables WHERE hypertable_name = :t"),
        {"t": table},
    ).scalar() > 0


def _is_cagg(conn, view: str) -> bool:
    return conn.execute(
        text("SELECT COUNT(*) FROM timescaledb_information.continuous_aggregates WHERE view_name = :v"),
        {"v": view},
    ).scalar() > 0


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        text("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = :t"),
        {"t": table},
    ).scalar() > 0


def _timescale_ok(conn) -> bool:
    return conn.execute(
        text("SELECT COUNT(*) FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar() > 0


def migrate():
    with engine.begin() as conn:
        if not _timescale_ok(conn):
            raise SystemExit("FAIL: timescaledb extension not installed — 分钟数据方案依赖 TimescaleDB")

        # 1. 基础表 + hypertable
        conn.execute(text(CREATE_STK_MINS))
        if not _is_hypertable(conn, "stk_mins_5min"):
            conn.execute(text(
                "SELECT create_hypertable('stk_mins_5min', 'trade_time', "
                "chunk_time_interval => INTERVAL '30 days')"
            ))
            print("Created hypertable stk_mins_5min (chunk 30 days)")
        else:
            print("stk_mins_5min hypertable already exists")

        # 2. 压缩配置声明（不挂策略——V1.1 时序纪律）
        conn.execute(text(COMPRESS_CONFIG.format(table="stk_mins_5min", col="trade_time")))
        print("Compression config declared on stk_mins_5min (NO policy attached)")

        # 3. 日级 cagg（WITH NO DATA）
        if not _is_cagg(conn, "stk_mins_1day"):
            conn.execute(text(CREATE_CAGG_1DAY))
            print("Created continuous aggregate stk_mins_1day (WITH NO DATA)")
        else:
            print("stk_mins_1day cagg already exists")
        conn.execute(text(COMPRESS_CONFIG_CAGG))
        print("Compression config declared on stk_mins_1day (NO policy attached)")

        # 4. 按股票断点表
        conn.execute(text(CREATE_CHECKPOINT))
        print("Ensured table sync_code_checkpoint")


def verify():
    ok = True
    with engine.connect() as conn:
        # 表/hypertable 存在性
        if not _is_hypertable(conn, "stk_mins_5min"):
            print("FAIL: stk_mins_5min is not a hypertable")
            ok = False
        else:
            interval = conn.execute(text(
                "SELECT time_interval FROM timescaledb_information.dimensions "
                "WHERE hypertable_name = 'stk_mins_5min' ORDER BY dimension_number LIMIT 1"
            )).scalar()
            status = "OK" if interval == __import__("datetime").timedelta(days=30) else "WARN"
            print(f"{'OK' if status == 'OK' else 'WARN'}: stk_mins_5min hypertable, chunk={interval}")

        # 压缩配置已声明（基础表走 hypertables 视图，cagg 走 continuous_aggregates 视图）
        enabled = conn.execute(text(
            "SELECT COUNT(*) FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'stk_mins_5min' AND compression_enabled = true"
        )).scalar()
        print(f"{'OK' if enabled else 'FAIL'}: stk_mins_5min compression_enabled={bool(enabled)}")
        ok = ok and bool(enabled)

        if _is_cagg(conn, "stk_mins_1day"):
            cagg_comp = conn.execute(text(
                "SELECT compression_enabled FROM timescaledb_information.continuous_aggregates "
                "WHERE view_name = 'stk_mins_1day'"
            )).scalar()
            print(f"{'OK' if cagg_comp else 'WARN'}: stk_mins_1day compression_enabled={bool(cagg_comp)}")

        # cagg 存在
        if not _is_cagg(conn, "stk_mins_1day"):
            print("FAIL: stk_mins_1day cagg missing")
            ok = False
        else:
            print("OK: stk_mins_1day cagg exists")

        # 断点表
        if not _table_exists(conn, "sync_code_checkpoint"):
            print("FAIL: sync_code_checkpoint missing")
            ok = False
        else:
            print("OK: sync_code_checkpoint exists")

        # V1.1 纪律：此阶段不得挂任何策略
        policies = conn.execute(text(
            "SELECT hypertable_name, proc_name FROM timescaledb_information.jobs "
            "WHERE hypertable_name IN ('stk_mins_5min', 'stk_mins_1day') "
            "AND proc_name IN ('policy_compression', 'policy_refresh_continuous_aggregate', 'policy_retention')"
        )).fetchall()
        if policies:
            print(f"WARN: policies already attached (finalize 之后属正常): {policies}")
        else:
            print("OK: no policies attached (等待回填完成后 --finalize)")

    print("VERIFY:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    if "--verify" in sys.argv:
        sys.exit(0 if verify() else 1)
    migrate()
    print()
    sys.exit(0 if verify() else 1)
