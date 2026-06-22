# -*- coding: utf-8 -*-
from fastapi import APIRouter
from data.database.connection import SessionLocal
from data.database.models import BacktestRun, TradeRecord, EquityCurve
from backtest.engine.vbt_engine import VBTEngine
from backtest.strategies.registry import StrategyRegistry

router = APIRouter()


@router.post("/run")
def run_backtest(
    strategy: str,
    symbols: list,
    start: str,
    end: str,
    strategy_config: dict = None,
    init_cash: float = 100000.0,
):
    engine = VBTEngine()
    try:
        run_id = engine.run(
            strategy_name=strategy,
            symbols=symbols,
            start=start,
            end=end,
            strategy_config=strategy_config,
            init_cash=init_cash,
        )
        return {"status": "completed", "run_id": run_id}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


@router.get("/status/{run_id}")
def backtest_status(run_id: int):
    session = SessionLocal()
    try:
        run = session.query(BacktestRun).get(run_id)
        if not run:
            return {"error": f"Run {run_id} not found"}
        return {
            "id": run.id,
            "strategy_name": run.strategy_name,
            "status": run.status,
            "total_return": run.total_return,
            "sharpe_ratio": run.sharpe_ratio,
            "max_drawdown": run.max_drawdown,
            "total_trades": run.total_trades,
            "final_value": run.final_value,
            "created_at": str(run.created_at),
            "completed_at": str(run.completed_at) if run.completed_at else None,
        }
    finally:
        session.close()


@router.get("/result/{run_id}")
def backtest_result(run_id: int):
    session = SessionLocal()
    try:
        run = session.query(BacktestRun).get(run_id)
        if not run:
            return {"error": f"Run {run_id} not found"}
        return {
            "id": run.id,
            "strategy_name": run.strategy_name,
            "strategy_params": run.strategy_params,
            "symbols": run.symbols,
            "start_date": str(run.start_date),
            "end_date": str(run.end_date),
            "init_cash": run.init_cash,
            "total_return": run.total_return,
            "annual_return": run.annual_return,
            "max_drawdown": run.max_drawdown,
            "sharpe_ratio": run.sharpe_ratio,
            "win_rate": run.win_rate,
            "total_trades": run.total_trades,
            "final_value": run.final_value,
            "result_json": run.result_json,
        }
    finally:
        session.close()


@router.get("/trades/{run_id}")
def backtest_trades(run_id: int, limit: int = 100):
    session = SessionLocal()
    try:
        trades = session.query(TradeRecord).filter_by(run_id=run_id).order_by(
            TradeRecord.entry_time
        ).limit(limit).all()
        return [
            {
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_time": str(t.entry_time),
                "exit_time": str(t.exit_time),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "size": t.size,
                "pnl": t.pnl,
                "return_pct": t.return_pct,
                "fees": t.fees,
            }
            for t in trades
        ]
    finally:
        session.close()


@router.get("/strategies")
def list_strategies():
    return [{"name": s} for s in StrategyRegistry.list_all()]


@router.get("/history")
def backtest_history(limit: int = 20):
    session = SessionLocal()
    try:
        runs = session.query(BacktestRun).order_by(
            BacktestRun.created_at.desc()
        ).limit(limit).all()
        return [
            {
                "id": r.id,
                "strategy_name": r.strategy_name,
                "total_return": r.total_return,
                "sharpe_ratio": r.sharpe_ratio,
                "max_drawdown": r.max_drawdown,
                "total_trades": r.total_trades,
                "status": r.status,
                "created_at": str(r.created_at),
            }
            for r in runs
        ]
    finally:
        session.close()
