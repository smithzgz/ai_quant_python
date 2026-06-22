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
