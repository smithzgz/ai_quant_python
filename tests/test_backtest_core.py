# -*- coding: utf-8 -*-
"""回测核心逻辑单元测试（不依赖数据库）：复权口径、报告/统计模块可用性。"""
import numpy as np
import pandas as pd
import pytest

from backtest.engine.vbt_engine import VBTEngine


def test_build_adjusted_close_uses_last_row_as_base():
    """前复权：以窗口末行因子为基准，末行复权价 == 原始收盘价。"""
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    close = pd.DataFrame({"A": [10.0, 12.0]}, index=idx)
    # 期间发生 1:2 拆分，因子从 1 变 2
    adj_factor = pd.DataFrame({"A": [1.0, 2.0]}, index=idx)

    out = VBTEngine.build_adjusted_close({"close": close, "adj_factor": adj_factor})
    assert out.iloc[-1, 0] == pytest.approx(12.0)      # 基准行不变
    assert out.iloc[0, 0] == pytest.approx(5.0)        # 前复权后历史价被压低


def test_build_adjusted_close_without_factor_returns_raw():
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    close = pd.DataFrame({"A": [10.0, 12.0]}, index=idx)
    out = VBTEngine.build_adjusted_close({"close": close})
    pd.testing.assert_frame_equal(out, close)


def test_build_adjusted_close_no_zero_division():
    """基准因子为 0 时退回原始价，不产生 inf。"""
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    close = pd.DataFrame({"A": [10.0, 12.0]}, index=idx)
    adj_factor = pd.DataFrame({"A": [1.0, 0.0]}, index=idx)
    out = VBTEngine.build_adjusted_close({"close": close, "adj_factor": adj_factor})
    assert out.iloc[0, 0] == pytest.approx(10.0)
    assert not np.isinf(out.values).any()


def test_qlib_report_and_stats_importable():
    """调研结论落地前提：qlib 报告/统计模块可用（plotly 后端，不依赖 matplotlib）。"""
    import qlib.contrib.report as qcr

    assert "analysis_position.report_graph" in qcr.GRAPH_NAME_LIST
    from qlib.contrib.evaluate import risk_analysis

    assert callable(risk_analysis)


def test_qlib_risk_analysis_on_synthetic_returns():
    """用 qlib 的风险分析对一段合成收益计算指标，验证链路可用。"""
    from qlib.contrib.evaluate import risk_analysis

    idx = pd.date_range("2024-01-01", periods=250, freq="B")
    rng = np.random.default_rng(0)
    rets = pd.Series(rng.normal(0.0005, 0.01, len(idx)), index=idx)
    res = risk_analysis(rets, freq="day")
    assert res is not None
    assert len(res) > 0


def test_compute_target_shares_is_integer_lots():
    """修正③：整手——所有股数必须为 0 或 100 的整数倍。"""
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    # 价格 50 → 1000 股；333 → 100 股；600 → 100 股(接近1手保护)；2000 → 0 股(过贵剔除)
    close = pd.DataFrame({"A": [50.0, 333.0], "B": [600.0, 2000.0]}, index=idx)
    shares = VBTEngine.compute_target_shares(close, alloc_value=50000.0)
    assert (shares.values % 100 == 0).all()
    assert shares.iloc[0, 0] == 1000          # 50 元 → 1000 股
    assert shares.iloc[0, 1] == 100           # 600 元 → 100 股(>=0.5手保护)
    assert shares.iloc[1, 1] == 0             # 2000 元 → 不足半手，剔除


def test_compute_target_shares_no_negative_or_nan():
    """整手计算不产生负数或 NaN。"""
    idx = pd.to_datetime(["2024-01-02", "2024-01-03"])
    close = pd.DataFrame({"A": [10.0, 0.0], "B": [np.nan, 100.0]}, index=idx)
    shares = VBTEngine.compute_target_shares(close, alloc_value=50000.0)
    assert (shares >= 0).all().all()
    assert not shares.isna().any().any()
