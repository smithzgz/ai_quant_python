# -*- coding: utf-8 -*-
class AShareBroker:
    COMMISSION_RATE = 0.00025
    COMMISSION_MIN = 5.0
    STAMP_DUTY_RATE = 0.0005
    TRANSFER_FEE_RATE = 0.00001
    LOT_SIZE = 100

    MAIN_BOARD_LIMIT = 0.10
    STAR_BOARD_LIMIT = 0.20
    ST_LIMIT = 0.05

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
        return AShareBroker.COMMISSION_RATE + AShareBroker.TRANSFER_FEE_RATE

    @staticmethod
    def get_vbt_slippage() -> float:
        return 0.001
