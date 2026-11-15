# -*- coding: utf-8 -*-
"""完整指标计算 + 逐笔交易落库状态 的验证用例。"""
import numpy as np
import pandas as pd
import pytest

from backtest.performance.metrics import calc_full_metrics, calc_metrics
from backtest.storage.persistor import BacktestPersistor


def _equity(n=252, start=100.0, drift=0.0005, seed=7):
    """确定性上升的净值序列，避免随机抽样导致的断言不稳定。"""
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 0.002, n)          # 零均值噪声，方向由 drift 决定
    r = drift + noise
    return pd.Series(start * (1 + r).cumprod(), index=idx)


# ---------------- calc_full_metrics ----------------

def test_full_metrics_groups_present():
    m = calc_full_metrics(_equity())
    assert set(["收益", "风险", "风险调整"]).issubset(m.keys())


def test_total_and_annual_return():
    eq = _equity(n=252)
    m = calc_full_metrics(eq)
    total = eq.iloc[-1] / eq.iloc[0] - 1
    assert m["收益"]["累计收益%"] == pytest.approx(total * 100, rel=1e-6)
    # 几何年化按 (1+累计)^(1/年数)-1，年数 = 收益率条数/252
    n_years = len(eq.pct_change().dropna()) / 252
    expect = (1 + total) ** (1 / n_years) - 1
    assert m["收益"]["几何年化%"] == pytest.approx(expect * 100, rel=1e-6)


def test_sharpe_positive_for_upward_series():
    m = calc_full_metrics(_equity(drift=0.0015))
    assert m["风险调整"]["夏普比率"] > 1.0


def test_sortino_not_understated_by_unit_error():
    """回归用例：曾误把已年化的下行波动再乘 sqrt(252)，导致索提诺被压到 0.14。

    构造：年化超额收益约 (0.001*252-3%)=22.2%，下行波动约 0.7%*sqrt(252)=11.1%，
    索提诺应约 2.0 量级，绝不可能小于 0.5。
    """
    rng = np.random.default_rng(11)
    n = 500
    r = np.where(rng.random(n) < 0.5, rng.normal(0.002, 0.0002, n), rng.normal(-0.0005, 0.0002, n))
    eq = pd.Series(1_000_000 * (1 + r).cumprod(), index=pd.date_range("2024-01-01", periods=n, freq="D"))
    s = calc_full_metrics(eq)["风险调整"]["索提诺比率"]
    assert s > 0.5, f"索提诺量纲错误: {s}"


def test_max_drawdown_negative_and_bounded():
    eq = _equity()
    m = calc_full_metrics(eq)
    dd = (eq / eq.cummax() - 1).min()
    assert m["风险"]["最大回撤%"] == pytest.approx(dd * 100, rel=1e-6)
    assert -100 < m["风险"]["最大回撤%"] <= 0


def test_empty_or_short_series_returns_empty():
    assert calc_full_metrics(pd.Series(dtype=float)) == {}
    assert calc_full_metrics(pd.Series([100.0])) == {}


def test_benchmark_block_computed():
    eq = _equity(n=300)
    bench = pd.Series(np.random.default_rng(3).normal(0.0002, 0.008, 299),
                      index=eq.index[1:])
    m = calc_full_metrics(eq, bench_returns=bench)
    assert "相对基准" in m
    assert "Beta" in m["相对基准"]
    assert "信息比率" in m["相对基准"]


def test_trade_stats_exclude_open_positions():
    """未平仓的浮动盈亏不得混入胜率/盈亏比统计。"""
    trades = pd.DataFrame({
        "pnl": [100.0, -50.0, 20.0, 5000.0],      # 最后一笔是未平仓浮盈
        "return_pct": [10.0, -5.0, 2.0, 400.0],
        "fees": [5.0, 5.0, 5.0, 5.0],
        "status": ["closed", "closed", "closed", "open"],
        "entry_time": pd.to_datetime(["2024-01-01"] * 4),
        "exit_time": pd.to_datetime(["2024-02-01"] * 4),
    })
    t = calc_full_metrics(_equity(), trades=trades, init_cash=1_000_000.0)["交易"]
    assert t["总笔数"] == 4
    assert t["已平仓"] == 3
    assert t["未平仓"] == 1
    assert t["未平仓浮盈(元)"] == pytest.approx(5000.0)
    # 胜率按已平仓 3 笔算：2 盈 1 亏 = 66.67%
    assert t["胜率%"] == pytest.approx(2 / 3 * 100, rel=1e-6)
    # 盈亏比 = 盈利和(120) / 亏损和(50) = 2.4，不含 5000 浮盈
    assert t["盈亏比"] == pytest.approx(120.0 / 50.0, rel=1e-6)


def test_all_open_positions_yields_nan_profit_factor_not_crash():
    trades = pd.DataFrame({
        "pnl": [10.0], "return_pct": [1.0], "fees": [1.0],
        "status": ["open"],
        "entry_time": pd.to_datetime(["2024-01-01"]),
        "exit_time": pd.to_datetime(["2024-02-01"]),
    })
    t = calc_full_metrics(_equity(), trades=trades, init_cash=1_000_000.0)["交易"]
    assert t["已平仓"] == 0
    assert np.isnan(t["盈亏比"])


def test_fee_ratio_uses_init_cash():
    trades = pd.DataFrame({
        "pnl": [10.0], "return_pct": [1.0], "fees": [2500.0], "status": ["closed"],
        "entry_time": pd.to_datetime(["2024-01-01"]),
        "exit_time": pd.to_datetime(["2024-02-01"]),
    })
    t = calc_full_metrics(_equity(), trades=trades, init_cash=1_000_000.0)["交易"]
    assert t["费用占初始资金%"] == pytest.approx(0.25, rel=1e-6)


# ---------------- 旧函数未被破坏 ----------------

def test_legacy_calc_metrics_still_works():
    m = calc_metrics(_equity())
    assert set(["total_return", "annual_return", "max_drawdown",
                "sharpe_ratio", "sortino_ratio", "calmar_ratio"]).issubset(m.keys())


# ---------------- persistor: 平仓状态 ----------------

def test_persistor_marks_open_and_closed():
    """vbt 对未平仓交易会填最后一根 bar 作为 Exit Timestamp、PnL 为浮盈；
    落库必须按 Status 区分，且未平仓的 exit_time 置空。"""
    df = pd.DataFrame({
        "Exit Trade Id": [0, 1],
        "Column": ["000001.SZ", "600519.SH"],
        "Size": [100.0, 200.0],
        "Entry Timestamp": pd.to_datetime(["2024-01-06", "2024-01-06"]),
        "Avg Entry Price": [10.0, 20.0],
        "Entry Fees": [1.0, 2.0],
        "Exit Timestamp": pd.to_datetime(["2024-01-31", "2024-02-29"]),  # 第2笔实为未平仓
        "Avg Exit Price": [12.0, 10.0],
        "Exit Fees": [1.5, 0.0],
        "PnL": [200.0, -2000.0],
        "Return": [0.2, -0.5],
        "Direction": ["Long", "Long"],
        "Status": ["Closed", "Open"],
        "Position Id": [0, 1],
    })

    captured = {}

    class FakeSession:
        def bulk_save_objects(self, records):
            captured["records"] = list(records)

    BacktestPersistor._save_trades(BacktestPersistor(), FakeSession(), 1, df)
    recs = captured["records"]
    assert len(recs) == 2

    closed = recs[0]
    assert closed.status == "closed"
    assert closed.exit_time is not None
    assert closed.symbol == "000001.SZ"
    assert closed.fees == pytest.approx(2.5)      # Entry + Exit

    opened = recs[1]
    assert opened.status == "open", "未平仓交易必须标记 open"
    assert opened.exit_time is None, "未平仓不应写入虚假出场时间"


def test_persistor_falls_back_to_nat_when_no_status_column():
    """没有 Status 列时，用 Exit Timestamp 是否为 NaT 兜底判断。"""
    df = pd.DataFrame({
        "Column": ["000001.SZ", "600519.SH"],
        "Size": [100.0, 200.0],
        "Entry Timestamp": pd.to_datetime(["2024-01-06", "2024-01-06"]),
        "Avg Entry Price": [10.0, 20.0],
        "Entry Fees": [1.0, 2.0],
        "Exit Timestamp": pd.to_datetime(["2024-01-31", None]),
        "Avg Exit Price": [12.0, None],
        "Exit Fees": [1.5, 0.0],
        "PnL": [200.0, -2000.0],
        "Return": [0.2, -0.5],
        "Direction": ["Long", "Long"],
        "Position Id": [0, 1],
    })

    captured = {}

    class FakeSession:
        def bulk_save_objects(self, records):
            captured["records"] = list(records)

    BacktestPersistor._save_trades(BacktestPersistor(), FakeSession(), 1, df)
    recs = captured["records"]
    assert recs[0].status == "closed"
    assert recs[1].status == "open"
    assert recs[1].exit_time is None
