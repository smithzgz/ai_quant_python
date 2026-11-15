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

    @staticmethod
    def compute_target_shares(close: pd.DataFrame, alloc_value: float) -> pd.DataFrame:
        """整手计算：按等权金额换算为 100 股整数倍。

        A 股最小交易单位为 100 股。以执行价(复权收盘)估算可买股数，向下取整到整百股；
        价格偏高导致不足 1 手(但已接近，>=0.5 手)时按 1 手处理(轻微超配，由现金约束兜底)；
        价格过高导致连 1 手都不到则不交易(0 股)。返回整型股数矩阵。
        """
        raw_shares = alloc_value / close.replace(0, np.nan)
        lots = (raw_shares // 100).fillna(0)
        lots = lots.where(~((lots == 0) & (raw_shares >= 50)), 1.0)
        return (lots * 100).fillna(0).astype(int)

    def run(self, strategy_name: str, symbols: list, start: str, end: str,
            strategy_config: dict = None, init_cash: float = 100000.0,
            commission_rate: float = None, slippage_rate: float = None,
            freq: str = "1D", precise_costs: bool = True) -> int:
        """运行回测。

        precise_costs=True（默认）时按 A 股真实成本计费：买入=佣金+过户费，
        卖出=佣金+过户费+印花税，并计入单笔最低佣金；通过 elementwise 费率矩阵传给 vectorbt。
        传入 commission_rate 或 precise_costs=False 时退回旧的单一边比例费（不含印花税）。
        """
        # commission_rate 为 None 表示"用默认成本模型"；显式传值则强制单一比例费
        if commission_rate is None:
            use_precise = precise_costs
            fee_meta = AShareBroker.get_vbt_fees()
        else:
            use_precise = False
            fee_meta = commission_rate

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
        alloc_value = init_cash / max(n_cols, 1)

        # 修正③：整手处理。A 股最小交易单位为 100 股，按等权金额换算为整百股。
        target_shares = self.compute_target_shares(adj_close, alloc_value)

        if use_precise:
            fees_arg = AShareBroker.build_fees_array(entries, exits, order_value=alloc_value)
            cost_model = "precise_ashare"
            logger.info(
                f"Cost model: A股分边精确成本(买={AShareBroker.fee_fraction(alloc_value,'buy')[1]:.6f}, "
                f"卖={AShareBroker.fee_fraction(alloc_value,'sell')[1]:.6f})"
            )
        else:
            fees_arg = fee_meta
            cost_model = "flat_vbt"
            logger.warning(f"Cost model: 单一比例费 {fee_meta}（不含卖出印花税，历史口径）")

        pf = vbt.Portfolio.from_signals(
            adj_close,
            entries=entries,
            exits=exits,
            init_cash=init_cash,
            freq=freq,
            fees=fees_arg,
            slippage=slippage_rate,
            accumulate=False,        # 修正①：每次信号只开一个仓位，杜绝金字塔加仓
            cash_sharing=True,
            group_by=True,
            size=target_shares,      # 修正③：整百股
            size_type="amount",
        )

        config = {
            "strategy_name": strategy_name,
            "strategy_params": strategy_config,
            "symbols": symbols,
            "start_date": start,
            "end_date": end,
            "init_cash": init_cash,
            "commission_rate": fee_meta,
            "slippage_rate": slippage_rate,
            "price_mode": "qfq_window",
            "cost_model": cost_model,
            "accumulate": False,
            "lot_rounding": True,
            "size_type": "shares",
        }

        run_id = self.persistor.save(pf, config)
        logger.info(f"Backtest completed: run_id={run_id}")
        return run_id
