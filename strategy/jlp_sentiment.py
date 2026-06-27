# -*- coding: utf-8 -*-
"""
研报评级变化策略 (JLP Sentiment Shift Strategy)

核心逻辑：
- 追踪每只股票的研报情绪变化（基于 target_space）
- 当情绪发生显著变化时产生交易信号
- 无变化时不交易
"""
import logging
import pandas as pd
import numpy as np
from typing import Optional

logger = logging.getLogger(__name__)


def prepare_sentiment_data(conn, ts_code: str = None) -> pd.DataFrame:
    """
    准备研报情绪数据。
    返回 DataFrame: columns = [trade_date, ts_code, avg_target, sentiment_ma, delta]
    """
    where = f"WHERE ts_code = '{ts_code}'" if ts_code else ""
    sql = f"""
        SELECT 
            comment_date AS trade_date,
            ts_code,
            AVG(CASE 
                WHEN target_space IS NOT NULL AND target_space != '--' AND target_space != '' 
                THEN CAST(REPLACE(REPLACE(target_space, '%', ''), ',', '') AS NUMERIC) 
                ELSE NULL 
            END) AS avg_target,
            COUNT(*) AS daily_count
        FROM sohu_jlp
        {where}
        GROUP BY comment_date, ts_code
        ORDER BY ts_code, comment_date
    """
    df = pd.read_sql(sql, conn)
    
    if df.empty:
        return df
    
    # Calculate sentiment moving average per stock
    df['sentiment_ma'] = df.groupby('ts_code')['avg_target'].transform(
        lambda x: x.rolling(window=20, min_periods=2).mean()
    )
    
    # Calculate delta (change vs 10 days ago)
    df['delta'] = df.groupby('ts_code')['sentiment_ma'].transform(
        lambda x: x.diff(10)
    )
    
    # Normalize delta as percentage change
    df['delta_pct'] = df['delta'] / df['sentiment_ma'].shift(20) * 100
    
    return df


def generate_signals(df: pd.DataFrame, 
                     threshold: float = 15.0,
                     min_records: int = 3) -> pd.DataFrame:
    """
    生成交易信号。
    
    Args:
        df: prepare_sentiment_data 返回的 DataFrame
        threshold: delta_pct 的阈值，超过则产生信号
        min_records: 每只股票最少需要的记录数
    
    Returns:
        DataFrame with columns: [trade_date, ts_code, signal]
        signal: 1=买入, -1=卖出, 0=无信号
    """
    if df.empty:
        return df
    
    # Filter stocks with enough data
    stock_counts = df.groupby('ts_code').size()
    valid_stocks = stock_counts[stock_counts >= min_records].index
    df = df[df['ts_code'].isin(valid_stocks)].copy()
    
    # Generate signals based on delta_pct
    df['signal'] = 0
    
    # Buy signal: sentiment upgraded significantly
    df.loc[df['delta_pct'] > threshold, 'signal'] = 1
    
    # Sell signal: sentiment downgraded significantly
    df.loc[df['delta_pct'] < -threshold, 'signal'] = -1
    
    # Keep only rows with signals
    signals_df = df[df['signal'] != 0][['trade_date', 'ts_code', 'signal', 'avg_target', 'delta_pct']].copy()
    
    # Deduplicate: for same stock, keep only first signal of each direction run
    if not signals_df.empty:
        signals_df = signals_df.sort_values(['ts_code', 'trade_date'])
        deduped = []
        for code, group in signals_df.groupby('ts_code'):
            prev_signal = 0
            for _, row in group.iterrows():
                if row['signal'] != prev_signal:
                    deduped.append(row)
                    prev_signal = row['signal']
        signals_df = pd.DataFrame(deduped) if deduped else signals_df.iloc[0:0]
    
    return signals_df


def get_daily_prices(conn, ts_codes: list, start_date: str = None, end_date: str = None) -> pd.DataFrame:
    """
    获取日线价格数据。
    返回 DataFrame: columns = [trade_date, ts_code, open, high, low, close, vol]
    """
    codes_str = "','".join(ts_codes)
    where_parts = [f"ts_code IN ('{codes_str}')"]
    if start_date:
        where_parts.append(f"trade_date >= '{start_date}'")
    if end_date:
        where_parts.append(f"trade_date <= '{end_date}'")
    
    where = " AND ".join(where_parts)
    
    sql = f"""
        SELECT 
            trade_date,
            ts_code,
            open,
            high,
            low,
            close,
            vol
        FROM daily
        WHERE {where}
        ORDER BY ts_code, trade_date
    """
    return pd.read_sql(sql, conn)


def run_backtest(conn,
                 threshold: float = 15.0,
                 stop_loss: float = 0.08,
                 max_positions: int = 10,
                 max_pct_per_stock: float = 0.05,
                 holding_days: int = 20,
                 start_date: str = '2022-01-01',
                 end_date: str = '2026-06-30'):
    """
    运行回测。
    
    Returns:
        dict with backtest results
    """
    import vectorbt as vbt
    
    logger.info("Preparing sentiment data...")
    sentiment_df = prepare_sentiment_data(conn)
    
    if sentiment_df.empty:
        return {'error': 'No sentiment data available'}
    
    logger.info(f"Sentiment data: {len(sentiment_df)} rows, {sentiment_df['ts_code'].nunique()} stocks")
    
    # Generate signals
    signals_df = generate_signals(sentiment_df, threshold=threshold)
    
    if signals_df.empty:
        return {'error': 'No signals generated'}
    
    logger.info(f"Signals: {len(signals_df)} total ({(signals_df['signal']==1).sum()} buy, {(signals_df['signal']==-1).sum()} sell)")
    
    # Get price data for stocks with signals
    ts_codes = signals_df['ts_code'].unique().tolist()
    prices_df = get_daily_prices(conn, ts_codes, start_date, end_date)
    
    if prices_df.empty:
        return {'error': 'No price data available'}
    
    logger.info(f"Price data: {len(prices_df)} rows")
    
    # Create price matrix (pivot)
    price_matrix = prices_df.pivot_table(
        index='trade_date', columns='ts_code', values='close'
    )
    price_matrix.index = pd.to_datetime(price_matrix.index)
    price_matrix = price_matrix.sort_index()
    
    # Create signal matrix
    signal_matrix = signals_df.pivot_table(
        index='trade_date', columns='ts_code', values='signal'
    )
    signal_matrix.index = pd.to_datetime(signal_matrix.index)
    signal_matrix = signal_matrix.sort_index()
    
    # Align matrices
    common_dates = price_matrix.index.intersection(signal_matrix.index)
    common_stocks = price_matrix.columns.intersection(signal_matrix.columns)
    price_matrix = price_matrix.loc[common_dates, common_stocks]
    signal_matrix = signal_matrix.loc[common_dates, common_stocks]
    
    # Fill signal matrix (forward fill, then fill NaN with 0)
    signal_matrix = signal_matrix.reindex(price_matrix.index).fillna(0)
    
    # Run VectorBT backtest
    # Entries: signal == 1 (buy)
    # Exits: signal == -1 (sell) or stop loss or holding period
    entries = signal_matrix == 1
    exits = signal_matrix == -1
    
    logger.info("Running VectorBT portfolio simulation...")
    
    pf = vbt.Portfolio.from_signals(
        price_matrix,
        entries=entries,
        exits=exits,
        init_cash=1000000,  # 100万初始资金
        fees=0.001,  # 0.1% 手续费
        slippage=0.001,  # 0.1% 滑点
        freq='1D',
    )
    
    def _safe_mean(val):
        if isinstance(val, pd.Series):
            clean = val.replace([np.inf, -np.inf], np.nan).dropna()
            return float(clean.mean()) if len(clean) > 0 else 0.0
        v = float(val)
        return v if np.isfinite(v) else 0.0

    # Portfolio-level metrics (use value curve, not per-column averages)
    portfolio_value = pf.value()
    total_return = float((portfolio_value.iloc[-1].sum() / portfolio_value.iloc[0].sum()) - 1)
    sharpe = _safe_mean(pf.sharpe_ratio())
    max_dd = _safe_mean(pf.max_drawdown())
    win_rate = _safe_mean(pf.trades.win_rate())
    num_trades = int(pf.trades.count().sum())
    
    # Per-stock stats from trade records (much faster than running individual portfolios)
    stock_returns = {}
    records = pf.trades.records_readable
    if not records.empty:
        for stock in common_stocks:
            stock_trades = records[records['Column'] == stock]
            if len(stock_trades) > 0:
                stock_returns[stock] = {
                    'return': float(stock_trades['Return'].mean()),
                    'trades': int(len(stock_trades)),
                }
    
    results = {
        'total_return': float(total_return),
        'sharpe_ratio': float(sharpe),
        'max_drawdown': float(max_dd),
        'win_rate': float(win_rate),
        'num_trades': int(num_trades),
        'threshold': threshold,
        'start_date': start_date,
        'end_date': end_date,
        'stocks_traded': len(common_stocks),
        'signal_count': int(signal_matrix.sum().sum()),
        'stock_returns': stock_returns,
    }
    
    return results


if __name__ == '__main__':
    import sys
    sys.path.insert(0, r'D:\code\Python\ai_quant_python')
    from config.settings import settings
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    
    import psycopg2
    conn = psycopg2.connect(
        host=settings.DB_HOST, port=settings.DB_PORT,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
        database=settings.DB_NAME
    )
    
    # Test data preparation
    df = prepare_sentiment_data(conn)
    print(f"\nSentiment data: {len(df)} rows")
    print(df.head(20))
    
    # Test signal generation
    signals = generate_signals(df, threshold=15.0)
    print(f"\nSignals: {len(signals)} rows")
    print(signals.head(20))
    
    conn.close()
