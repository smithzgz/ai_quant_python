# -*- coding: utf-8 -*-
import json
import pandas as pd
import numpy as np
from datetime import datetime, date, timezone
from data.database.connection import SessionLocal
from data.database.models import BacktestRun, TradeRecord, EquityCurve
from backtest.engine.result_extractor import extract_results
from utils.logger import get_logger

logger = get_logger("persistor")


def _sanitize_json(obj):
    if obj is None:
        return None
    if isinstance(obj, float) and (pd.isna(obj) or obj == float('inf') or obj == float('-inf')):
        return None
    if isinstance(obj, pd.Timedelta):
        return str(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat() if hasattr(obj, 'isoformat') else str(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        if pd.isna(obj):
            return None
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_json(v) for v in obj]
    return obj


def _safe_float(val):
    if val is None:
        return None
    if isinstance(val, float) and (pd.isna(val) or val == float('inf') or val == float('-inf')):
        return None
    return val


class BacktestPersistor:
    def save(self, pf, config: dict) -> int:
        results = extract_results(pf, config)
        stats = results["stats"]

        session = SessionLocal()
        try:
            run = BacktestRun(
                strategy_name=config["strategy_name"],
                strategy_params=config.get("strategy_params", {}),
                symbols=config.get("symbols", []),
                start_date=config["start_date"],
                end_date=config["end_date"],
                init_cash=config.get("init_cash", 100000.0),
                commission_rate=config.get("commission_rate", 0.00025),
                slippage_rate=config.get("slippage_rate", 0.001),
                status="completed",
                total_return=_safe_float(stats.get("total_return")),
                annual_return=_safe_float(stats.get("annual_return")),
                max_drawdown=_safe_float(stats.get("max_drawdown")),
                sharpe_ratio=_safe_float(stats.get("sharpe_ratio")),
                sortino_ratio=_safe_float(stats.get("sortino_ratio")),
                win_rate=_safe_float(stats.get("win_rate")),
                profit_factor=_safe_float(stats.get("profit_factor")),
                total_trades=stats.get("total_trades"),
                final_value=_safe_float(stats.get("final_value")),
                result_json=_sanitize_json(results.get("result_json")),
                completed_at=datetime.now(timezone.utc),
            )
            session.add(run)
            session.flush()
            run_id = run.id

            self._save_trades(session, run_id, results.get("trades_df"))

            self._save_equity(session, run_id, results.get("equity"),
                              results.get("drawdown"), results.get("returns"))

            session.commit()
            logger.info(f"Backtest saved: run_id={run_id}, trades={stats.get('total_trades', 0)}")
            return run_id

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save backtest: {e}")
            raise
        finally:
            session.close()

    def _save_trades(self, session, run_id: int, trades_df):
        if trades_df is None or trades_df.empty:
            return

        records = []
        for idx, row in trades_df.iterrows():
            entry_time = self._parse_vbt_time(row.get("Entry Index"))
            exit_time = self._parse_vbt_time(row.get("Exit Index"))

            col_val = str(row.get("Column", ""))
            symbol = self._extract_symbol(col_val)

            records.append(TradeRecord(
                run_id=run_id,
                trade_idx=idx,
                symbol=symbol,
                direction="long",
                entry_time=entry_time,
                exit_time=exit_time,
                entry_price=float(row.get("Avg Entry Price", 0)),
                exit_price=float(row.get("Avg Exit Price", 0)),
                size=float(row.get("Size", 0)),
                pnl=float(row.get("PnL", 0)),
                return_pct=float(row.get("Return", 0)) * 100 if abs(row.get("Return", 0)) < 1 else float(row.get("Return", 0)),
                fees=float(row.get("Fees", 0)),
                duration_bars=int(row.get("Duration", 0)) if pd.notna(row.get("Duration")) else None,
            ))

        session.bulk_save_objects(records)

    @staticmethod
    def _extract_symbol(col_val: str) -> str:
        if not col_val:
            return ""
        for part in col_val.replace("(", ",").replace(")", ",").split(","):
            part = part.strip().strip("'\" ")
            if "." in part and any(c.isdigit() for c in part):
                return part
        return col_val

    def _save_equity(self, session, run_id: int, equity, drawdown, returns):
        if equity is None:
            return

        records = []

        if isinstance(equity, pd.DataFrame):
            equity_series = equity.mean(axis=1)
        else:
            equity_series = equity

        if drawdown is not None and isinstance(drawdown, pd.DataFrame):
            drawdown = drawdown.mean(axis=1)

        if returns is not None and isinstance(returns, pd.DataFrame):
            returns = returns.mean(axis=1)

        for ts, val in equity_series.items():
            dd_val = float(drawdown.loc[ts]) if drawdown is not None and ts in drawdown.index else 0
            ret_val = float(returns.loc[ts]) if returns is not None and ts in returns.index else 0

            records.append(EquityCurve(
                run_id=run_id,
                timestamp=self._parse_vbt_time(ts),
                equity_value=float(val),
                drawdown=float(dd_val) if not pd.isna(dd_val) else 0,
                daily_return=float(ret_val) * 100 if not pd.isna(ret_val) and abs(ret_val) < 1 else float(ret_val) if not pd.isna(ret_val) else 0,
            ))

        session.bulk_save_objects(records)

    @staticmethod
    def _parse_vbt_time(val):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        if isinstance(val, pd.Timestamp):
            return val.to_pydatetime()
        if isinstance(val, (datetime, date)):
            return val
        try:
            return pd.Timestamp(val).to_pydatetime()
        except Exception:
            return None
