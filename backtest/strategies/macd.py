# -*- coding: utf-8 -*-
import vectorbt as vbt
import pandas as pd
from backtest.strategies.base import StrategyBase
from backtest.strategies.registry import StrategyRegistry


@StrategyRegistry.register
class MACD(StrategyBase):
    name = "macd"
    description = "MACD strategy - buy when MACD crosses above signal, sell when below"

    def get_default_config(self) -> dict:
        return {"fast_window": 12, "slow_window": 26, "signal_window": 9}

    def generate_signals(self, data: dict, config: dict) -> tuple:
        fast_window = config.get("fast_window", 12)
        slow_window = config.get("slow_window", 26)
        signal_window = config.get("signal_window", 9)

        close = data["close"]

        macd = vbt.MACD.run(close, fast_window=fast_window,
                             slow_window=slow_window, signal_window=signal_window)

        entries = macd.macd_crossed_above(macd.signal)
        exits = macd.macd_crossed_below(macd.signal)

        return entries, exits
