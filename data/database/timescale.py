# -*- coding: utf-8 -*-
from sqlalchemy import text
from data.database.connection import engine, SessionLocal
from data.database.models import Base
from utils.logger import get_logger

logger = get_logger("timescale")

TIMESCALE_AVAILABLE = False


def check_timescale():
    global TIMESCALE_AVAILABLE
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT COUNT(*) FROM pg_available_extensions WHERE name='timescaledb'")
            ).scalar()
            TIMESCALE_AVAILABLE = result > 0
            if TIMESCALE_AVAILABLE:
                logger.info("TimescaleDB extension available")
            else:
                logger.warning("TimescaleDB not available, using plain PostgreSQL")
    except Exception:
        logger.warning("TimescaleDB not available, using plain PostgreSQL")
        TIMESCALE_AVAILABLE = False


def setup_timescale():
    if not TIMESCALE_AVAILABLE:
        logger.info("Skipping TimescaleDB setup (not installed)")
        return

    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        conn.commit()
        logger.info("TimescaleDB extension ensured")

    hypertables = [
        ("daily", "trade_date", "7 days"),
        ("daily_basic", "trade_date", "7 days"),
        ("adj_factor", "trade_date", "7 days"),
        ("fund_daily", "trade_date", "30 days"),
    ]

    with engine.connect() as conn:
        for table, time_col, chunk_interval in hypertables:
            try:
                exists = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM timescaledb_information.hypertables "
                        "WHERE hypertable_name = :table"
                    ),
                    {"table": table},
                ).scalar()
                if not exists:
                    conn.execute(
                        text(
                            f"SELECT create_hypertable('{table}', '{time_col}', "
                            f"chunk_time_interval => INTERVAL '{chunk_interval}', "
                            f"migrate_data => true)"
                        )
                    )
                    logger.info(f"Created hypertable: {table}")
                else:
                    logger.info(f"Hypertable already exists: {table}")
            except Exception as e:
                logger.warning(f"Skipping hypertable for {table}: {e}")

        compression_policies = [
            ("daily", "90 days"),
            ("daily_basic", "90 days"),
            ("adj_factor", "90 days"),
            ("fund_daily", "180 days"),
        ]

        for table, compress_after in compression_policies:
            try:
                conn.execute(
                    text(
                        f"ALTER TABLE {table} SET (timescaledb.compress, "
                        f"timescaledb.compress_segmentby = 'ts_code', "
                        f"timescaledb.compress_orderby = 'trade_date DESC')"
                    )
                )
                conn.execute(
                    text(
                        f"SELECT add_compression_policy('{table}', INTERVAL '{compress_after}')"
                    )
                )
                logger.info(f"Compression policy set for {table}")
            except Exception as e:
                if "already exists" in str(e) or "duplicate" in str(e).lower():
                    pass
                else:
                    logger.warning(f"Compression policy skipped for {table}: {e}")

        conn.commit()


def init_all_tables():
    Base.metadata.create_all(engine)
    logger.info("All tables created")
    check_timescale()
    setup_timescale()
