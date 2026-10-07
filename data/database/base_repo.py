# -*- coding: utf-8 -*-
from sqlalchemy import text
from data.database.connection import engine


class BaseRepo:
    def __init__(self, model_class):
        self.model = model_class

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
