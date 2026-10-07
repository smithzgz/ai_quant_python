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

        has_new_schema = "Entry Timestamp" in trades_df.columns
        records = []
        for idx, row in trades_df.iterrows():
            if has_new_schema:
                entry_time = self._parse_vbt_time(row.get("Entry Timestamp"))
                exit_time = self._parse_vbt_time(row.get("Exit Timestamp"))
                fees = float(row.get("Entry Fees") or 0) + float(row.get("Exit Fees") or 0)
                direction = str(row.get("Direction") or "long").lower()
            else:
                entry_time = self._parse_vbt_time(row.get("Entry Index"))
                exit_time = self._parse_vbt_time(row.get("Exit Index"))
                fees = float(row.get("Fees") or 0)
                direction = "long"

            col_val = str(row.get("Column", ""))
            symbol = self._extract_symbol(col_val)

            ret = row.get("Return")
            ret = float(ret) if pd.notna(ret) else None

            exit_price = row.get("Avg Exit Price")
            exit_price = float(exit_price) if pd.notna(exit_price) else None

            records.append(TradeRecord(
                run_id=run_id,
                trade_idx=idx,
                symbol=symbol,
                direction=direction,
                entry_time=entry_time,
                exit_time=exit_time,
                entry_price=float(row.get("Avg Entry Price") or 0),
                exit_price=exit_price,
                size=float(row.get("Size") or 0),
                pnl=float(row.get("PnL") or 0),
                return_pct=ret * 100.0 if ret is not None else None,
                fees=fees,
                duration_bars=None,
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

        if isinstance(equity, pd.DataFrame):
            equity_series = equity.iloc[:, 0] if equity.shape[1] == 1 else equity.sum(axis=1)
        else:
            equity_series = equity

        equity_series = equity_series.astype(float)

        if isinstance(drawdown, pd.Series):
            dd_series = drawdown.astype(float)
        else:
            dd_series = equity_series / equity_series.cummax() - 1.0

        if isinstance(returns, pd.Series):
            ret_series = returns.astype(float)
        else:
            ret_series = equity_series.pct_change().fillna(0.0)

        records = []
        for ts, val in equity_series.items():
            dd_val = float(dd_series.loc[ts]) if ts in dd_series.index else 0.0
            ret_val = float(ret_series.loc[ts]) if ts in ret_series.index else 0.0
            if pd.isna(dd_val):
                dd_val = 0.0
            if pd.isna(ret_val):
                ret_val = 0.0

            records.append(EquityCurve(
                run_id=run_id,
                timestamp=self._parse_vbt_time(ts),
                equity_value=float(val),
                drawdown=dd_val * 100.0,
                daily_return=ret_val * 100.0,
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
