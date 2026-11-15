# -*- coding: utf-8 -*-
"""样本外 universe 构造函数的单元测试（不依赖网络：成分读本地JSON，流动性查本地DB）。"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.run_oos_backtest import (  # noqa: E402
    filter_present,
    load_hs300_asof,
    pick_liq_pt20,
)

START, END = "2023-01-01", "2025-12-31"


def test_load_hs300_asof_returns_point_in_time_list():
    codes = load_hs300_asof()
    assert isinstance(codes, list)
    assert len(codes) == 300
    # 成分格式应为 tushare 代码
    assert all(c.endswith((".SH", ".SZ")) for c in codes)
    assert "600519.SH" in codes  # 贵州茅台应属沪深300


def test_filter_present_only_keeps_window_symbols():
    codes = load_hs300_asof()
    present = filter_present(codes, START, END)
    assert len(present) == 300  # 全部在窗口内有日线
    assert set(present).issubset(set(codes))


def test_filter_present_drops_unknown():
    mixed = ["600519.SH", "999999.SH"]  # 后者不存在
    present = filter_present(mixed, START, END)
    assert present == ["600519.SH"]


def test_pick_liq_pt20_returns_valid_codes():
    codes = pick_liq_pt20(START)
    assert isinstance(codes, list)
    assert 1 <= len(codes) <= 20
    # 这些代码在窗口内应有日线数据
    present = filter_present(codes, START, END)
    assert len(present) == len(codes)
