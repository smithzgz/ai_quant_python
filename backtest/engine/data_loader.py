# -*- coding: utf-8 -*-
import pandas as pd
from sqlalchemy import text
from data.database.connection import engine
from utils.logger import get_logger

logger = get_logger("data_loader")


class DataLoader:
    def load(self, symbols: list, start: str, end: str) -> dict:
        if isinstance(start, str):
            start = pd.Timestamp(start).strftime("%Y-%m-%d")
        if isinstance(end, str):
            end = pd.Timestamp(end).strftime("%Y-%m-%d")

        sql = text("""
            SELECT d.ts_code, d.trade_date,
                   d.open, d.high, d.low, d.close, d.vol as volume,
                   d.pre_close, d.pct_chg, d.amount,
                   db.pe_ttm, db.pb, db.turnover_rate, db.total_mv,
                   af.adj_factor
            FROM daily d
            LEFT JOIN daily_basic db ON d.ts_code = db.ts_code AND d.trade_date = db.trade_date
            LEFT JOIN adj_factor af ON d.ts_code = af.ts_code AND d.trade_date = af.trade_date
            WHERE d.ts_code = ANY(:symbols)
              AND d.trade_date BETWEEN :start AND :end
            ORDER BY d.ts_code, d.trade_date
        """)

        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"symbols": symbols, "start": start, "end": end})

        if df.empty:
            logger.warning(f"No data loaded for {symbols} between {start} and {end}")
            return {}

        df["trade_date"] = pd.to_datetime(df["trade_date"])

        result = {}
        price_cols = ["open", "high", "low", "close", "volume", "pre_close", "pct_chg", "amount",
                      "pe_ttm", "pb", "turnover_rate", "total_mv", "adj_factor"]

        for col in price_cols:
            if col in df.columns:
                pivot = df.pivot(index="trade_date", columns="ts_code", values=col)
                result[col] = pivot.sort_index()

        return result

    def load_adj_close(self, symbols: list, start: str, end: str) -> pd.DataFrame:
        data = self.load(symbols, start, end)
        if not data or "close" not in data or "adj_factor" not in data:
            return pd.DataFrame()

        close = data["close"]
        adj_factor = data["adj_factor"]

        adj_close = close * adj_factor / adj_factor.iloc[-1]
        adj_close = adj_close.replace([float("inf"), float("-inf")], float("nan"))
        return adj_close.fillna(method="ffill")
