# -*- coding: utf-8 -*-
from sqlalchemy import text
from data.database.connection import engine, SessionLocal
from utils.logger import get_logger

logger = get_logger("base_repo")


class BaseRepo:
    def __init__(self, model_class):
        self.model = model_class

    def bulk_upsert_df(self, df, table_name: str, pk_cols: list):
        if df is None or df.empty:
            return 0

        rows_before = 0
        with engine.connect() as conn:
            try:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                rows_before = result.scalar()
            except Exception:
                pass

        df.to_sql(
            table_name,
            engine,
            if_exists="append",
            index=False,
            method="multi",
        )

        rows_after = 0
        with engine.connect() as conn:
            try:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                rows_after = result.scalar()
            except Exception:
                pass

        inserted = rows_after - rows_before
        logger.info(f"bulk_upsert_df {table_name}: {len(df)} rows written, {inserted} new")
        return len(df)

    def get_latest_date(self, table_name: str, date_col: str = "trade_date"):
        with engine.connect() as conn:
            try:
                result = conn.execute(
                    text(f"SELECT MAX({date_col}) FROM {table_name}")
                ).scalar()
                return result
            except Exception:
                return None

    def count_rows(self, table_name: str):
        with engine.connect() as conn:
            try:
                return conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
            except Exception:
                return 0
