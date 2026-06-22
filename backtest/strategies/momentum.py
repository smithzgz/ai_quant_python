# -*- coding: utf-8 -*-
import vectorbt as vbt
import pandas as pd
from backtest.strategies.base import StrategyBase
from backtest.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class Momentum(StrategyBase):
    name = "momentum"
    description = "动量策略 - 过去N日涨幅排名前K买入"

    def get_default_config(self) -> dict:
        return {"lookback": 20, "top_k": 10}

    def generate_signals(self, data: dict, config: dict) -> tuple:
        lookback = config.get("lookback", 20)
        top_k = config.get("top_k", 10)

        close = data["close"]
        returns = close.pct_change(lookback)

        entries = returns.rank(axis=1, ascending=False) <= top_k
        exits = returns.rank(axis=1, ascending=False) > top_k

        return entries, exits
