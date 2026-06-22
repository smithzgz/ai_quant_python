# -*- coding: utf-8 -*-
import pandas as pd


class StrategyBase:
    name: str = "base"
    description: str = ""

    def generate_signals(self, data: dict, config: dict) -> tuple:
        raise NotImplementedError

    def get_default_config(self) -> dict:
        return {}
