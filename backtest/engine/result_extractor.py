# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import vectorbt as vbt
from datetime import datetime, date
from data.database.connection import SessionLocal
from data.database.models import BacktestRun, TradeRecord, EquityCurve
from backtest.broker.a_share import AShareBroker
from utils.logger import get_logger

logger = get_logger("result_extractor")


def extract_results(pf: vbt.Portfolio, config: dict) -> dict:
    stats = pf.stats()

    result = {
        "total_return": float(stats.get("Total Return [%]", 0)),
        "annual_return": float(stats.get("Annualized Return [%]", 0)) if "Annualized Return [%]" in stats else None,
        "max_drawdown": float(stats.get("Max Drawdown [%]", 0)),
        "sharpe_ratio": float(stats.get("Sharpe Ratio", 0)) if "Sharpe Ratio" in stats else None,
        "sortino_ratio": float(stats.get("Sortino Ratio", 0)) if "Sortino Ratio" in stats else None,
        "win_rate": float(stats.get("Win Rate [%]", 0)) if "Win Rate [%]" in stats else None,
        "profit_factor": float(stats.get("Profit Factor", 0)) if "Profit Factor" in stats else None,
        "total_trades": int(stats.get("Total Trades", 0)) if "Total Trades" in stats else 0,
        "final_value": float(stats.get("End Value", 0)) if "End Value" in stats else None,
        "start_date": str(stats.get("Start", "")) if "Start" in stats else None,
        "end_date": str(stats.get("End", "")) if "End" in stats else None,
    }

    trades_df = None
    try:
        trades_df = pf.trades.records_readable
    except Exception as e:
        logger.warning(f"Failed to extract trades: {e}")

    equity = None
    try:
        equity = pf.value()
    except Exception as e:
        logger.warning(f"Failed to extract equity: {e}")

    drawdown = None
    try:
        drawdown = pf.drawdown()
    except Exception:
        pass

    returns = None
    try:
        returns = pf.returns()
    except Exception:
        pass

    return {
        "stats": result,
        "trades_df": trades_df,
        "equity": equity,
        "drawdown": drawdown,
        "returns": returns,
        "result_json": stats.to_dict(),
    }
