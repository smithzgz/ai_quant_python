# -*- coding: utf-8 -*-
import vectorbt as vbt
import pandas as pd
from backtest.strategies.base import StrategyBase
from backtest.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class MACross(StrategyBase):
    name = "ma_cross"
    description = "双均线交叉策略"

    def get_default_config(self) -> dict:
        return {"fast_window": 10, "slow_window": 50}

    def generate_signals(self, data: dict, config: dict) -> tuple:
        fast_window = config.get("fast_window", 10)
        slow_window = config.get("slow_window", 50)

        close = data["close"]

        fast_ma = vbt.MA.run(close, fast_window)
        slow_ma = vbt.MA.run(close, slow_window)

        entries = fast_ma.ma_crossed_above(slow_ma)
        exits = fast_ma.ma_crossed_below(slow_ma)

        return entries, exits
