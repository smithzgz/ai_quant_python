# -*- coding: utf-8 -*-
"""A 股成本模型单元测试（不依赖数据库）。

覆盖 P0：卖出印花税、最低佣金、买卖分边费率矩阵。
"""
import numpy as np
import pandas as pd
import pytest

from backtest.broker.a_share import AShareBroker


def test_commission_min_binding():
    """小额成交额触发最低 5 元佣金。"""
    assert AShareBroker.calc_commission(1000.0) == 5.0          # 0.25 -> 5.0
    assert AShareBroker.calc_commission(0.0) == 5.0


def test_commission_proportional():
    """大额成交额按比例。"""
    assert AShareBroker.calc_commission(100000.0) == pytest.approx(25.0)


def test_buy_side_fees():
    """买入 = 佣金 + 过户费（无印花税）。"""
    amount = 100000.0
    expected = 25.0 + 100000.0 * 0.00001
    assert AShareBroker.calc_side_fees(amount, "buy") == pytest.approx(expected)
    fee, frac = AShareBroker.fee_fraction(amount, "buy")
    assert fee == pytest.approx(expected)
    assert frac == pytest.approx(0.00026)


def test_sell_side_fees_include_stamp_duty():
    """卖出 = 佣金 + 过户费 + 印花税。这是旧实现漏掉的部分。"""
    amount = 100000.0
    expected = 25.0 + 100000.0 * 0.00001 + 100000.0 * 0.0005
    assert AShareBroker.calc_side_fees(amount, "sell") == pytest.approx(expected)
    _, frac = AShareBroker.fee_fraction(amount, "sell")
    assert frac == pytest.approx(0.00076)


def test_stamp_duty_is_sell_only():
    """印花税只作用于卖出，买卖差正好等于印花税额。"""
    amount = 100000.0
    diff = AShareBroker.calc_side_fees(amount, "sell") - AShareBroker.calc_side_fees(amount, "buy")
    assert diff == pytest.approx(amount * AShareBroker.STAMP_DUTY_RATE)


def test_min_commission_raises_small_order_fraction():
    """小额订单：最低佣金使实际费率远高于名义费率。"""
    _, frac_small = AShareBroker.fee_fraction(1000.0, "buy")
    _, frac_big = AShareBroker.fee_fraction(100000.0, "buy")
    assert frac_small > frac_big
    assert frac_small == pytest.approx((5.0 + 0.01) / 1000.0)


def test_build_fees_array_side_specific():
    """费率矩阵：买单=买费率，卖单=卖费率，无信号=0。"""
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    cols = ["A", "B"]
    entries = pd.DataFrame(False, index=idx, columns=cols)
    exits = pd.DataFrame(False, index=idx, columns=cols)
    entries.iloc[0] = True
    exits.iloc[3] = True

    order_value = 100000.0
    fees = AShareBroker.build_fees_array(entries, exits, order_value)

    assert fees.shape == (5, 2)
    # 买入行 = 买费率
    assert fees.iloc[0].tolist() == pytest.approx([0.00026, 0.00026])
    # 卖出行 = 卖费率（含印花税）
    assert fees.iloc[3].tolist() == pytest.approx([0.00076, 0.00076])
    # 无信号行 = 0
    assert fees.iloc[1].tolist() == pytest.approx([0.0, 0.0])
    assert fees.iloc[2].tolist() == pytest.approx([0.0, 0.0])


def test_fees_array_zero_when_no_signal():
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    entries = pd.DataFrame(False, index=idx, columns=["A"])
    exits = pd.DataFrame(False, index=idx, columns=["A"])
    fees = AShareBroker.build_fees_array(entries, exits, 50000.0)
    assert float(fees.values.sum()) == 0.0


def test_round_to_lot():
    assert AShareBroker.round_to_lot(350) == 300
    assert AShareBroker.round_to_lot(100) == 100
    assert AShareBroker.round_to_lot(99) == 0


def test_legacy_flat_fee_excludes_stamp_duty():
    """旧口径 get_vbt_fees() 确实不含印花税——保留此断言以固定该历史缺陷。"""
    flat = AShareBroker.get_vbt_fees()
    assert flat == pytest.approx(0.00026)
    assert flat < AShareBroker.fee_fraction(100000.0, "sell")[1]
