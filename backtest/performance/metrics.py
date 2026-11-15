# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd


def calc_metrics(equity_curve: pd.Series, risk_free_rate: float = 0.03) -> dict:
    if equity_curve is None or equity_curve.empty:
        return {}

    returns = equity_curve.pct_change().dropna()
    total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1

    n_years = len(equity_curve) / 252
    annual_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0

    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    max_drawdown = drawdown.min()

    excess_returns = returns - risk_free_rate / 252
    sharpe = np.sqrt(252) * excess_returns.mean() / excess_returns.std() if excess_returns.std() > 0 else 0

    downside = returns[returns < 0]
    sortino = np.sqrt(252) * excess_returns.mean() / downside.std() if len(downside) > 0 and downside.std() > 0 else 0

    calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0

    return {
        "total_return": total_return * 100,
        "annual_return": annual_return * 100,
        "max_drawdown": max_drawdown * 100,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
    }


# ---------------------------------------------------------------------------
# 完整指标体系（供回测报告使用）
# ---------------------------------------------------------------------------

TRADING_DAYS = 252


def _max_dd_duration_days(equity: pd.Series) -> int:
    """最长回撤持续天数：从历史高点回落到重新创新高的最长跨度。"""
    if equity.empty:
        return 0
    peak = equity.iloc[0]
    peak_i = 0
    best = 0
    cur_start = None
    for i, v in enumerate(equity.values):
        if v >= peak:
            if cur_start is not None:
                best = max(best, i - cur_start)
                cur_start = None
            peak, peak_i = v, i
        else:
            if cur_start is None:
                cur_start = peak_i
    if cur_start is not None:
        best = max(best, len(equity) - 1 - cur_start)
    return int(best)


def calc_full_metrics(
    equity: pd.Series,
    bench_returns: pd.Series = None,
    trades: pd.DataFrame = None,
    init_cash: float = None,
    risk_free_rate: float = 0.03,
) -> dict:
    """计算回测报告需要的完整常规指标。

    Args:
        equity: 净值曲线（金额）。
        bench_returns: 基准日收益序列（index 为日期，与 equity 对齐后取交集）。
        trades: 逐笔交易明细，需含列 pnl / return_pct / fees / entry_time / exit_time。
        init_cash: 初始资金，用于算费用占比。
        risk_free_rate: 年化无风险利率，用于夏普/索提诺。

    Returns:
        分组字典：{"收益": {...}, "风险": {...}, "风险调整": {...}, "相对基准": {...}, "交易": {...}}
    """
    out: dict = {}
    if equity is None or equity.empty or len(equity) < 2:
        return out

    rets = equity.pct_change().dropna()
    if rets.empty:
        return out

    n = len(rets)
    years = n / TRADING_DAYS
    total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1)
    geo_ann = float((1 + total_ret) ** (1 / years) - 1) if years > 0 else 0.0
    arith_ann = float(rets.mean() * TRADING_DAYS)
    vol = float(rets.std() * np.sqrt(TRADING_DAYS))
    downside = rets[rets < 0]
    down_vol = float(downside.std() * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else 0.0

    dd = equity / equity.cummax() - 1
    mdd = float(dd.min())
    mdd_days = _max_dd_duration_days(equity)

    rf_daily = risk_free_rate / TRADING_DAYS
    excess = rets - rf_daily
    sharpe = float(excess.mean() * TRADING_DAYS / vol) if vol > 0 else 0.0
    # down_vol 已是年化值，分子也用年化超额收益，两者量纲一致
    sortino = float(excess.mean() * TRADING_DAYS / down_vol) if down_vol > 0 else 0.0
    calmar = float(geo_ann / abs(mdd)) if mdd != 0 else 0.0
    var95 = float(np.percentile(rets, 5))
    cvar95 = float(rets[rets <= var95].mean()) if (rets <= var95).any() else var95
    omega = float(rets[rets > rf_daily].sum() / abs(rets[rets <= rf_daily].sum())) \
        if (rets <= rf_daily).any() and abs(rets[rets <= rf_daily].sum()) > 0 else np.nan

    out["收益"] = {
        "累计收益%": total_ret * 100,
        "几何年化%": geo_ann * 100,
        "算术年化%": arith_ann * 100,
        "终值": float(equity.iloc[-1]),
    }
    out["风险"] = {
        "年化波动%": vol * 100,
        "下行波动%": down_vol * 100,
        "最大回撤%": mdd * 100,
        "最长回撤(交易日)": mdd_days,
        "日VaR95%": var95 * 100,
        "日CVaR95%": cvar95 * 100,
    }
    out["风险调整"] = {
        "夏普比率": sharpe,
        "索提诺比率": sortino,
        "卡玛比率": calmar,
        "Omega比率": omega,
    }

    # ---- 相对基准 ----
    if bench_returns is not None and not bench_returns.empty:
        b = bench_returns.dropna()
        b = b.reindex(rets.index).dropna()
        r_aligned = rets.reindex(b.index).dropna()
        b = b.reindex(r_aligned.index)
        if len(r_aligned) > 2 and b.std() > 0:
            beta = float(np.cov(r_aligned, b)[0][1] / np.var(b, ddof=1))
            alpha_daily = float(r_aligned.mean() - beta * b.mean())
            alpha_ann = float(alpha_daily * TRADING_DAYS)
            te = float((r_aligned - b).std() * np.sqrt(TRADING_DAYS))
            ir = float((r_aligned - b).mean() * TRADING_DAYS / te) if te > 0 else 0.0
            bench_total = float((1 + b).prod() - 1)
            out["相对基准"] = {
                "基准累计收益%": bench_total * 100,
                "超额收益%": (total_ret - bench_total) * 100,
                "Alpha(年化)%": alpha_ann * 100,
                "Beta": beta,
                "跟踪误差%": te * 100,
                "信息比率": ir,
                "日胜率%": float((r_aligned > b).mean() * 100),
            }

    # ---- 交易统计 ----
    if trades is not None and not trades.empty:
        t = trades.copy()
        # 只统计已平仓：未平仓的 pnl 是浮动盈亏，混入会虚高胜率/盈亏比
        if "status" in t.columns:
            closed = t[t["status"].astype(str).str.lower() == "closed"]
            opened = t[t["status"].astype(str).str.lower() == "open"]
        else:
            closed, opened = t, t.iloc[0:0]
        pnl = pd.to_numeric(closed.get("pnl"), errors="coerce").dropna()
        rp = pd.to_numeric(closed.get("return_pct"), errors="coerce").dropna()
        wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
        fees = pd.to_numeric(t.get("fees"), errors="coerce").fillna(0)

        hold = np.nan
        if {"entry_time", "exit_time"}.issubset(t.columns):
            d = (pd.to_datetime(closed["exit_time"]) - pd.to_datetime(closed["entry_time"]))
            hold = float(d.dt.days.mean()) if len(d.dropna()) else np.nan

        out["交易"] = {
            "总笔数": int(len(t)),
            "已平仓": int(len(closed)),
            "未平仓": int(len(opened)),
            "未平仓浮盈(元)": float(pd.to_numeric(opened.get("pnl"), errors="coerce").fillna(0).sum()),
            "胜率%": float(len(wins) / len(pnl) * 100) if len(pnl) else 0.0,
            "盈亏比": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else np.nan,
            "平均盈利%": float(rp[rp > 0].mean()) if (rp > 0).any() else np.nan,
            "平均亏损%": float(rp[rp <= 0].mean()) if (rp <= 0).any() else np.nan,
            "单笔期望(元)": float(pnl.mean()) if len(pnl) else 0.0,
            "最佳单笔%": float(rp.max()) if len(rp) else np.nan,
            "最差单笔%": float(rp.min()) if len(rp) else np.nan,
            "平均持有(自然日)": hold,
            "总费用(元)": float(fees.sum()),
            "费用占初始资金%": float(fees.sum() / init_cash * 100) if init_cash else np.nan,
        }

    return out
