# -*- coding: utf-8 -*-
import importlib
import pkgutil
from backtest.strategies.base import StrategyBase
from utils.logger import get_logger

logger = get_logger("registry")


class StrategyRegistry:
    _strategies = {}

    @classmethod
    def register(cls, strategy_class):
        cls._strategies[strategy_class.name] = strategy_class
        logger.info(f"Strategy registered: {strategy_class.name}")
        return strategy_class

    @classmethod
    def get(cls, name: str) -> StrategyBase:
        if name not in cls._strategies:
            cls.auto_discover("backtest.strategies")
        if name not in cls._strategies:
            raise ValueError(f"Strategy not found: {name}. Available: {list(cls._strategies.keys())}")
        return cls._strategies[name]()

    @classmethod
    def list_all(cls) -> list:
        if not cls._strategies:
            cls.auto_discover("backtest.strategies")
        return list(cls._strategies.keys())

    @classmethod
    def auto_discover(cls, package_path: str):
        try:
            package = importlib.import_module(package_path)
            for _, mod_name, _ in pkgutil.iter_modules(package.__path__):
                try:
                    importlib.import_module(f"{package_path}.{mod_name}")
                except Exception as e:
                    logger.warning(f"Failed to load strategy module {mod_name}: {e}")
        except Exception as e:
            logger.warning(f"Auto-discover failed for {package_path}: {e}")
