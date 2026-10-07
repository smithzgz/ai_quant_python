# -*- coding: utf-8 -*-
import vectorbt as vbt
import pandas as pd
from backtest.strategies.base import StrategyBase
from backtest.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class Momentum(StrategyBase):
    name = "momentum"
    description = "动量策略 - 过去N日涨幅排名进入前K时买入，跌出前K时卖出"

    def get_default_config(self) -> dict:
        return {"lookback": 20, "top_k": 10}

    def generate_signals(self, data: dict, config: dict) -> tuple:
        lookback = config.get("lookback", 20)
        top_k = config.get("top_k", 10)

        close = data.get("adj_close", data.get("close"))
        returns = close.pct_change(lookback)

        rank = returns.rank(axis=1, ascending=False)
        in_top = rank <= top_k
        was_top = in_top.shift(1).fillna(False).astype(bool)

        entries = in_top & ~was_top
        exits = ~in_top & was_top

        return entries, exits
