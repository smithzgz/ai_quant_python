# -*- coding: utf-8 -*-
"""A股执行级模拟器（5min 精度）— MINUTE_DATA_PLAN.md V1.2 §7.2。

路线：预计算订单流 + vbt.Portfolio.from_orders
（three_framework_shared_pool.py 已验证该路线与手工模拟一致）。

规则：
- 信号日 D 的次一交易日 D' 首根 5min bar 开盘价成交（近似开盘/竞价）；
- 涨跌停可成交性：D' 开盘价触及/越过 prev_close×(1±limit) 则该方向跳过
  （主板 ±10%，创业板/科创板 ±20%）；
- 整手：买入 size = floor(槽位资金/成交价/100)×100 股；
- T+1：仅可卖出严格早于卖出日的建仓（按交易日计）；
- 估值用 qfq close 序列；qfq 空间内涨跌停判定等价（除权日近似，见 plan）。

输入：
- bars: {ts_code: DataFrame(index=trade_time(5min, tz-aware), columns 含 open/close)}
- daily_entries/daily_exits: bool DataFrame(index=交易日, columns=ts_code)
"""
import numpy as np
import pandas as pd
import vectorbt as vbt

from backtest.broker.a_share import AShareBroker
from utils.logger import get_logger

logger = get_logger("execution_sim")


def limit_ratio_for(ts_code: str) -> float:
    """涨跌停幅度：创业板 300/301 与科创板 688 为 20%，其余 10%（ST 未处理）。"""
    code = ts_code.split(".")[0]
    if code.startswith(("300", "301", "688")):
        return 0.20
    return 0.10


class ExecutionSimulator:
    def __init__(self, init_cash: float = 1_000_000.0,
                 commission_rate: float = None, slippage_rate: float = None,
                 n_slots: int = 5):
        self.init_cash = init_cash
        self.commission_rate = commission_rate if commission_rate is not None \
            else AShareBroker.get_vbt_fees()
        self.slippage_rate = slippage_rate if slippage_rate is not None \
            else AShareBroker.get_vbt_slippage()
        self.n_slots = max(n_slots, 1)
        self.slot_cash = init_cash / self.n_slots

    # ---------- 预处理 ----------

    @staticmethod
    def _day_frame(bars: pd.DataFrame):
        """按日聚合：首根 bar 开盘、末根 bar 收盘。"""
        grp = bars.groupby(bars.index.date)
        day_open = grp["open"].first()
        day_close = grp["close"].last()
        first_ts = grp.apply(lambda g: g.index[0])
        return day_open, day_close, first_ts

    def _build_orders(self, ts_code: str, bars: pd.DataFrame,
                      entries: pd.Series, exits: pd.Series) -> pd.DataFrame:
        day_open, day_close, first_ts = self._day_frame(bars)
        days = list(day_open.index)
        day_pos = {d: i for i, d in enumerate(days)}
        limit = limit_ratio_for(ts_code)

        orders = []
        position = 0            # 当前持仓（股）

        def exec_day(signal_day) -> int:
            """信号日 -> 执行日序号（次一交易日）。"""
            key = pd.Timestamp(signal_day).date()
            i = day_pos.get(key)
            if i is None or i + 1 >= len(days):
                return -1
            return i + 1

        # --- 买入（entries）---
        if entries is not None:
            for signal_day in entries.index[entries.fillna(False)]:
                i = exec_day(signal_day)
                if i < 0:
                    continue
                d = days[i]
                prev_close = day_close.iloc[i - 1]
                open_px = day_open.iloc[i]
                if prev_close <= 0 or open_px >= prev_close * (1 + limit) - 1e-9:
                    logger.debug(f"{ts_code} {d} limit-up open, buy skipped")
                    continue
                shares = int(self.slot_cash / open_px / 100) * 100
                if shares <= 0:
                    continue
                orders.append({
                    "ts_code": ts_code, "trade_time": first_ts.loc[d],
                    "size": shares, "price": open_px, "day": d,
                })
                position += shares

        # --- 卖出（exits，T+1）---
        if exits is not None:
            for signal_day in exits.index[exits.fillna(False)]:
                i = exec_day(signal_day)
                if i < 0:
                    continue
                d = days[i]
                # T+1：仅可卖严格早于 d 的建仓（orders 中 day < d 的净买入）
                bought = sum(o["size"] for o in orders if o["day"] < d and o["size"] > 0)
                sold = -sum(o["size"] for o in orders if o["day"] < d and o["size"] < 0)
                sellable = min(bought - sold, position)
                if sellable <= 0:
                    continue
                prev_close = day_close.iloc[i - 1]
                open_px = day_open.iloc[i]
                if prev_close <= 0 or open_px <= prev_close * (1 - limit) + 1e-9:
                    logger.debug(f"{ts_code} {d} limit-down open, sell skipped")
                    continue
                shares = int(sellable / 100) * 100
                if shares <= 0:
                    continue
                orders.append({
                    "ts_code": ts_code, "trade_time": first_ts.loc[d],
                    "size": -shares, "price": open_px, "day": d,
                })
                position -= shares

        return pd.DataFrame(orders)

    # ---------- 执行 ----------

    def run(self, bars: dict, daily_entries: pd.DataFrame,
            daily_exits: pd.DataFrame) -> dict:
        codes = list(bars.keys())
        close_df = pd.DataFrame({c: bars[c]["close"] for c in codes})
        open_df = pd.DataFrame({c: bars[c]["open"] for c in codes})
        idx = close_df.index

        for e in (daily_entries, daily_exits):
            if e is not None:
                idx_dates = set(idx.date)
                missing = [d for d in e.index if pd.Timestamp(d).date() not in idx_dates]
                if missing:
                    logger.warning(f"{len(missing)} signal days not in bar index, e.g. {missing[:3]}")

        all_orders = []
        for code in codes:
            ent = daily_entries[code] if daily_entries is not None and code in daily_entries.columns else None
            ext = daily_exits[code] if daily_exits is not None and code in daily_exits.columns else None
            all_orders.append(self._build_orders(code, bars[code], ent, ext))
        orders_df = pd.concat([o for o in all_orders if not o.empty], ignore_index=True) \
            if any(len(o) for o in all_orders) else pd.DataFrame(
            columns=["ts_code", "trade_time", "size", "price", "day"])

        # size/price 矩阵（NaN = 无订单）
        size_mat = pd.DataFrame(np.nan, index=idx, columns=codes)
        price_mat = pd.DataFrame(np.nan, index=idx, columns=codes)
        for _, o in orders_df.iterrows():
            size_mat.loc[o["trade_time"], o["ts_code"]] = o["size"]
            price_mat.loc[o["trade_time"], o["ts_code"]] = o["price"]

        pf = vbt.Portfolio.from_orders(
            close=close_df,
            size=size_mat,
            price=price_mat,
            fees=self.commission_rate,
            slippage=self.slippage_rate,
            init_cash=self.init_cash,
            cash_sharing=True,
            group_by=True,
            freq="5min",
        )

        logger.info(f"execution_sim: {len(orders_df)} orders over {len(codes)} codes")
        return {"portfolio": pf, "orders": orders_df,
                "close": close_df, "size_matrix": size_mat}
