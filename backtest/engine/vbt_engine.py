# -*- coding: utf-8 -*-
import vectorbt as vbt
import pandas as pd
import numpy as np
from backtest.engine.data_loader import DataLoader
from backtest.broker.a_share import AShareBroker
from backtest.strategies.registry import StrategyRegistry
from backtest.storage.persistor import BacktestPersistor
from utils.logger import get_logger

logger = get_logger("vbt_engine")


class VBTEngine:
    def __init__(self):
        self.data_loader = DataLoader()
        self.persistor = BacktestPersistor()

    @staticmethod
    def build_adjusted_close(data: dict) -> pd.DataFrame:
        close = data["close"]
        adj_factor = data.get("adj_factor")
        if adj_factor is None or adj_factor.empty:
            return close

        af = adj_factor.reindex(index=close.index, columns=close.columns).ffill()
        base = af.iloc[-1]
        adj_close = close * af / base
        adj_close = adj_close.where(af.notna() & base.notna() & (base != 0), close)
        adj_close = adj_close.replace([np.inf, -np.inf], np.nan)
        return adj_close

    def run(self, strategy_name: str, symbols: list, start: str, end: str,
            strategy_config: dict = None, init_cash: float = 100000.0,
            commission_rate: float = None, slippage_rate: float = None) -> int:

        if commission_rate is None:
            commission_rate = AShareBroker.get_vbt_fees()
        if slippage_rate is None:
            slippage_rate = AShareBroker.get_vbt_slippage()

        logger.info(f"Running backtest: {strategy_name} | {len(symbols)} symbols | {start} ~ {end}")

        strategy = StrategyRegistry.get(strategy_name)
        if strategy_config is None:
            strategy_config = strategy.get_default_config()

        data = self.data_loader.load(symbols, start, end)
        if not data or data.get("close") is None or data["close"].empty:
            raise ValueError(f"No price data available for {symbols}")

        adj_close = self.build_adjusted_close(data)
        data["adj_close"] = adj_close

        entries, exits = strategy.generate_signals(data, strategy_config)

        n_cols = adj_close.shape[1]
        pf = vbt.Portfolio.from_signals(
            adj_close,
            entries=entries,
            exits=exits,
            init_cash=init_cash,
            freq="1D",
            fees=commission_rate,
            slippage=slippage_rate,
            accumulate=True,
            cash_sharing=True,
            group_by=True,
            size=init_cash / max(n_cols, 1),
            size_type="value",
        )

        config = {
            "strategy_name": strategy_name,
            "strategy_params": strategy_config,
            "symbols": symbols,
            "start_date": start,
            "end_date": end,
            "init_cash": init_cash,
            "commission_rate": commission_rate,
            "slippage_rate": slippage_rate,
            "price_mode": "qfq_window",
        }

        run_id = self.persistor.save(pf, config)
        logger.info(f"Backtest completed: run_id={run_id}")
        return run_id
