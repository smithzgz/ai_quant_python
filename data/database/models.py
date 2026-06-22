# -*- coding: utf-8 -*-
from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, Date, DateTime, Text, Boolean,
    Index, UniqueConstraint, ForeignKey
)
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone
from data.database.connection import Base


def _now():
    return datetime.now(timezone.utc)


class TradeCal(Base):
    __tablename__ = "trade_cal"

    exchange = Column(String(10), primary_key=True)
    cal_date = Column(Date, primary_key=True)
    is_open = Column(Integer)
    pretrade_date = Column(Date)


class StockBasic(Base):
    __tablename__ = "stock_basic"

    ts_code = Column(String(20), primary_key=True)
    symbol = Column(String(10))
    name = Column(String(50))
    area = Column(String(20))
    industry = Column(String(50))
    fullname = Column(String(100))
    market = Column(String(10))
    exchange = Column(String(10))
    curr_type = Column(String(10))
    list_status = Column(String(10))
    list_date = Column(Date)
    delist_date = Column(Date)
    is_hs = Column(String(5))


class Daily(Base):
    __tablename__ = "daily"

    ts_code = Column(String(20), primary_key=True)
    trade_date = Column(Date, primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    pre_close = Column(Float)
    change = Column(Float)
    pct_chg = Column(Float)
    vol = Column(Float)
    amount = Column(Float)


class DailyBasic(Base):
    __tablename__ = "daily_basic"

    ts_code = Column(String(20), primary_key=True)
    trade_date = Column(Date, primary_key=True)
    close = Column(Float)
    turnover_rate = Column(Float)
    turnover_rate_f = Column(Float)
    volume_ratio = Column(Float)
    pe = Column(Float)
    pe_ttm = Column(Float)
    pb = Column(Float)
    ps = Column(Float)
    ps_ttm = Column(Float)
    dv_ratio = Column(Float)
    dv_ttm = Column(Float)
    total_share = Column(Float)
    float_share = Column(Float)
    free_share = Column(Float)
    total_mv = Column(Float)
    circ_mv = Column(Float)


class AdjFactor(Base):
    __tablename__ = "adj_factor"

    ts_code = Column(String(20), primary_key=True)
    trade_date = Column(Date, primary_key=True)
    adj_factor = Column(Float)


class FundBasic(Base):
    __tablename__ = "fund_basic"

    ts_code = Column(String(20), primary_key=True)
    name = Column(String(50))
    management = Column(String(50))
    custodian = Column(String(50))
    fund_type = Column(String(20))
    found_date = Column(Date)
    due_date = Column(Date)
    list_date = Column(Date)
    issue_date = Column(Date)
    delist_date = Column(Date)
    issue_amount = Column(Float)
    m_fee = Column(Float)
    c_fee = Column(Float)
    duration_year = Column(Float)
    p_value = Column(Float)
    min_amount = Column(Float)
    exp_return = Column(Float)
    benchmark = Column(Text)
    status = Column(String(10))
    invest_type = Column(String(20))
    type = Column("type", String(20))
    trustee = Column(String(50))
    purc_startdate = Column(Date)
    redm_startdate = Column(Date)
    market = Column(String(5))


class FundDaily(Base):
    __tablename__ = "fund_daily"

    ts_code = Column(String(20), primary_key=True)
    trade_date = Column(Date, primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    pre_close = Column(Float)
    change = Column(Float)
    pct_chg = Column(Float)
    vol = Column(Float)
    amount = Column(Float)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_name = Column(String(100), nullable=False)
    strategy_params = Column(JSONB, nullable=False, default=dict)
    symbols = Column(JSONB, nullable=False, default=list)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    init_cash = Column(Float, default=100000.0)
    commission_rate = Column(Float, default=0.00025)
    slippage_rate = Column(Float, default=0.001)
    status = Column(String(20), default="pending")

    total_return = Column(Float)
    annual_return = Column(Float)
    max_drawdown = Column(Float)
    sharpe_ratio = Column(Float)
    sortino_ratio = Column(Float)
    calmar_ratio = Column(Float)
    win_rate = Column(Float)
    profit_factor = Column(Float)
    total_trades = Column(Integer)
    final_value = Column(Float)

    result_json = Column(JSONB)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=_now)
    completed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_br_strategy", "strategy_name"),
        Index("idx_br_status", "status"),
        Index("idx_br_created", "created_at"),
    )


class TradeRecord(Base):
    __tablename__ = "trade_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False)
    trade_idx = Column(Integer)
    symbol = Column(String(50), nullable=False)
    direction = Column(String(10), nullable=False)
    entry_time = Column(DateTime(timezone=True))
    exit_time = Column(DateTime(timezone=True))
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float)
    size = Column(Float, nullable=False)
    pnl = Column(Float, default=0)
    return_pct = Column(Float, default=0)
    fees = Column(Float, default=0)
    duration_bars = Column(Integer)
    status = Column(String(20), default="closed")

    __table_args__ = (
        Index("idx_tr_run", "run_id"),
        Index("idx_tr_symbol", "symbol"),
        Index("idx_tr_entry", "entry_time"),
    )


class EquityCurve(Base):
    __tablename__ = "equity_curves"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    equity_value = Column(Float, nullable=False)
    drawdown = Column(Float, default=0)
    daily_return = Column(Float, default=0)
    cash_value = Column(Float, default=0)
    positions_value = Column(Float, default=0)

    __table_args__ = (
        Index("idx_ec_run_ts", "run_id", "timestamp"),
    )


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    symbol = Column(String(50), nullable=False)
    quantity = Column(Float, nullable=False)
    avg_cost = Column(Float, nullable=False)
    market_value = Column(Float, nullable=False)
    weight = Column(Float)
    unrealized_pnl = Column(Float, default=0)

    __table_args__ = (
        Index("idx_ps_run_ts", "run_id", "timestamp"),
    )


class SyncLog(Base):
    __tablename__ = "sync_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    table_name = Column(String(100), nullable=False)
    sync_mode = Column(String(20), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True))
    status = Column(String(20), nullable=False)
    rows_fetched = Column(Integer, default=0)
    rows_inserted = Column(Integer, default=0)
    error_message = Column(Text)
    details = Column(JSONB, default=dict)

    __table_args__ = (
        Index("idx_sl_table_time", "table_name", "start_time"),
    )


class SyncCheckpoint(Base):
    __tablename__ = "sync_checkpoint"

    table_name = Column(String(100), primary_key=True)
    last_sync_date = Column(Date, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_now)


class DataQualityLog(Base):
    __tablename__ = "data_quality_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    table_name = Column(String(100), nullable=False)
    check_date = Column(Date, nullable=False)
    rule_name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)
    total_rows = Column(Integer)
    issue_count = Column(Integer)
    details = Column(JSONB, default=list)
    checked_at = Column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        Index("idx_dql_table_date", "table_name", "check_date"),
    )
