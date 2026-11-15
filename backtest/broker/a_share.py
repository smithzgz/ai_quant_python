# -*- coding: utf-8 -*-
"""A 股券商成本模型。

费率口径（与 Qlib 的 exchange_kwargs: open_cost/close_cost/min_cost 对齐）：
- 佣金：双边，比例 COMMISSION_RATE，单笔最低 COMMISSION_MIN 元
- 印花税：仅卖出单边 STAMP_DUTY_RATE
- 过户费：双边 TRANSFER_FEE_RATE
- 整手：LOT_SIZE 股

历史遗留：``get_vbt_fees()`` 只返回"佣金+过户费"的单一比例，而 vectorbt 的 fees
对买卖双边同等收取 —— 这导致**卖出印花税被漏算、最低佣金未生效**，回测收益系统性偏乐观。
新增的 ``build_fees_array()`` 用 elementwise 费率矩阵修正该问题（见 vbt_engine）。
"""
import pandas as pd


class AShareBroker:
    COMMISSION_RATE = 0.00025
    COMMISSION_MIN = 5.0
    STAMP_DUTY_RATE = 0.0005
    TRANSFER_FEE_RATE = 0.00001
    LOT_SIZE = 100

    MAIN_BOARD_LIMIT = 0.10
    STAR_BOARD_LIMIT = 0.20
    ST_LIMIT = 0.05

    # ---------- 原有接口（保持兼容：execution_sim / 旧路径仍在用） ----------

    @staticmethod
    def calc_commission(amount: float) -> float:
        return max(amount * AShareBroker.COMMISSION_RATE, AShareBroker.COMMISSION_MIN)

    @staticmethod
    def calc_stamp_duty(amount: float) -> float:
        return amount * AShareBroker.STAMP_DUTY_RATE

    @staticmethod
    def calc_total_fees(buy_amount: float, sell_amount: float) -> float:
        buy_comm = AShareBroker.calc_commission(buy_amount)
        sell_comm = AShareBroker.calc_commission(sell_amount)
        stamp = AShareBroker.calc_stamp_duty(sell_amount)
        transfer = (buy_amount + sell_amount) * AShareBroker.TRANSFER_FEE_RATE
        return buy_comm + sell_comm + stamp + transfer

    @staticmethod
    def round_to_lot(shares: int) -> int:
        return (shares // AShareBroker.LOT_SIZE) * AShareBroker.LOT_SIZE

    @staticmethod
    def get_vbt_fees() -> float:
        """旧口径：单一比例费（佣金+过户费，不含印花税、不含最低佣金）。仅用于兼容。"""
        return AShareBroker.COMMISSION_RATE + AShareBroker.TRANSFER_FEE_RATE

    @staticmethod
    def get_vbt_slippage() -> float:
        return 0.001

    # ---------- 新增：买卖分边精确成本 ----------

    @staticmethod
    def calc_side_fees(amount: float, side: str) -> float:
        """单边费用额。side: 'buy' | 'sell'。"""
        if amount <= 0:
            return 0.0
        fees = AShareBroker.calc_commission(amount) + amount * AShareBroker.TRANSFER_FEE_RATE
        if side == "sell":
            fees += amount * AShareBroker.STAMP_DUTY_RATE
        return fees

    @staticmethod
    def fee_fraction(amount: float, side: str) -> tuple:
        """返回 (费用额, 费率)。费率供 vectorbt 的 fees 参数使用（按成交额比例计收）。"""
        if amount <= 0:
            return 0.0, 0.0
        total = AShareBroker.calc_side_fees(amount, side)
        return total, total / amount

    @staticmethod
    def build_fees_array(entries: pd.DataFrame, exits: pd.DataFrame, order_value: float) -> pd.DataFrame:
        """构造 elementwise 费率矩阵，供 vectorbt from_signals 使用。

        - 买入信号单元格 -> 买费率（佣金+过户费，含最低佣金折算）
        - 卖出信号单元格 -> 卖费率（再叠加印花税）
        - 其余单元格     -> 0（无成交，不产生费用）

        order_value: 代表性单笔成交金额（等权策略下取 init_cash / 标的数）。
        它只影响"最低 5 元佣金"触发与否的折算：当 order_value >= 20000 时
        最低佣金不生效，费率退化为纯比例，近似误差为 0。
        """
        buy_frac = AShareBroker.fee_fraction(order_value, "buy")[1]
        sell_frac = AShareBroker.fee_fraction(order_value, "sell")[1]

        fees = pd.DataFrame(0.0, index=entries.index, columns=entries.columns)
        e = entries.fillna(False).astype(bool)
        x = exits.fillna(False).astype(bool)
        fees[e] = buy_frac
        fees[x] = sell_frac
        return fees
