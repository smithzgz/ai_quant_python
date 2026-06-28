# -*- coding: utf-8 -*-
"""
双均线策略：Qlib vs VectorBT 对比
===================================

策略说明（面向初学者）：
------------------------
双均线策略（Dual Moving Average）是最经典的趋势跟踪策略之一：

1. 计算两条移动平均线：
   - 短期均线（如5日线）：反映近期价格趋势
   - 长期均线（如20日线）：反映中期价格趋势

2. 产生交易信号：
   - 买入信号：短期均线从下向上穿越长期均线（金叉）
   - 卖出信号：短期均线从上向下穿越长期均线（死叉）

3. 逻辑解释：
   - 金叉意味着近期价格开始强于中期价格，趋势可能向上
   - 死叉意味着近期价格开始弱于中期价格，趋势可能向下

使用方法：
----------
python strategy/backtest/dual_ma_compare.py
"""

import sys
import time
import logging
import pandas as pd
import numpy as np

sys.path.insert(0, r'D:\code\Python\ai_quant_python')

from config.settings import settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


# ============================================================
# 第一部分：数据准备（两个框架共用）
# ============================================================

def load_data_from_db(start_date='2020-01-01', end_date='2026-06-30', top_n=50):
    """
    从数据库加载股票日线数据
    
    参数:
        start_date: 回测开始日期
        end_date: 回测结束日期
        top_n: 选择成交量最大的N只股票
    
    返回:
        close_df: 收盘价DataFrame（日期为索引，股票代码为列）
    """
    import psycopg2
    
    logger.info(f"从数据库加载数据: {start_date} ~ {end_date}, Top {top_n} stocks")
    
    conn = psycopg2.connect(
        host=settings.DB_HOST, port=settings.DB_PORT,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
        database=settings.DB_NAME
    )
    
    # 先选出成交量最大的N只股票
    sql = f"""
        SELECT ts_code, SUM(vol) as total_vol
        FROM daily
        WHERE trade_date >= '{start_date.replace('-','')}'
          AND trade_date <= '{end_date.replace('-','')}'
        GROUP BY ts_code
        HAVING COUNT(*) > 100
        ORDER BY total_vol DESC
        LIMIT {top_n}
    """
    top_codes = pd.read_sql(sql, conn)['ts_code'].tolist()
    logger.info(f"选出 {len(top_codes)} 只流动性最好的股票")
    
    # 加载这些股票的日线数据
    codes_str = "','".join(top_codes)
    sql = f"""
        SELECT trade_date, ts_code, open, high, low, close, vol
        FROM daily
        WHERE ts_code IN ('{codes_str}')
          AND trade_date >= '{start_date.replace('-','')}'
          AND trade_date <= '{end_date.replace('-','')}'
        ORDER BY ts_code, trade_date
    """
    df = pd.read_sql(sql, conn)
    conn.close()
    
    logger.info(f"加载 {len(df)} 条记录, {df['ts_code'].nunique()} 只股票")
    
    # 转换日期格式
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    
    # 创建收盘价透视表（VectorBT和Qlib都需要这种格式）
    close_df = df.pivot_table(
        index='trade_date', 
        columns='ts_code', 
        values='close'
    )
    close_df = close_df.sort_index()
    
    logger.info(f"价格矩阵: {close_df.shape[0]} 天 x {close_df.shape[1]} 只股票")
    
    return close_df, df


# ============================================================
# 第二部分：VectorBT 实现
# ============================================================

def run_vectorbt_backtest(close_df, fast_window=5, slow_window=20, fees=0.001, slippage=0.001):
    """
    使用 VectorBT 实现双均线策略
    
    VectorBT 是什么？
    -----------------
    VectorBT 是一个高性能的向量化回测库。它的特点是：
    - 使用 NumPy/Pandas 向量化操作，速度快
    - 一行代码就能生成复杂的交易信号
    - 内置完整的组合管理和绩效分析
    
    工作原理：
    ---------
    1. 计算快速均线和慢速均线
    2. 当快速均线 > 慢速均线时，标记为持仓（True）
    3. 当快速均线 < 慢速均线时，标记为空仓（False）
    4. VectorBT 自动计算每次交易的收益
    """
    import vectorbt as vbt
    
    logger.info(f"\n{'='*60}")
    logger.info("VectorBT 双均线策略回测")
    logger.info(f"{'='*60}")
    logger.info(f"参数: 快线={fast_window}日, 慢线={slow_window}日")
    
    start_time = time.time()
    
    # 第2步：计算移动平均线
    fast_ma = close_df.rolling(window=fast_window).mean()
    slow_ma = close_df.rolling(window=slow_window).mean()
    
    logger.info(f"计算均线耗时: {time.time()-start_time:.3f}秒")
    
    # 第3步：生成交易信号
    # 金叉（快线上穿慢线）→ 买入, 死叉（快线下穿慢线）→ 卖出
    prev_fast = fast_ma.shift(1)
    prev_slow = slow_ma.shift(1)
    entries = (prev_fast <= prev_slow) & (fast_ma > slow_ma)   # 金叉
    exits = (prev_fast >= prev_slow) & (fast_ma < slow_ma)    # 死叉
    
    signal_time = time.time() - start_time
    logger.info(f"生成信号耗时: {signal_time:.3f}秒")
    
    # 第3步：运行回测
    # VectorBT 的 Portfolio 类会自动处理：
    # - 仓位管理（每只股票分配多少资金）
    # - 手续费计算
    # - 滑点模拟
    # - 绩效统计
    start_bt = time.time()
    
    pf = vbt.Portfolio.from_signals(
        close_df,           # 价格数据
        entries=entries,    # 买入信号
        exits=exits,        # 卖出信号
        init_cash=1_000_000,  # 初始资金100万
        fees=0.001,         # 手续费0.1%（买入和卖出各收一次）
        slippage=0.001,     # 滑点0.1%（实际成交价比理论价差一点）
        freq='1D',          # 频率：日线
    )
    
    bt_time = time.time() - start_bt
    total_time = time.time() - start_time
    
    logger.info(f"回测计算耗时: {bt_time:.3f}秒")
    logger.info(f"总耗时: {total_time:.3f}秒")
    
    # 第4步：提取绩效指标
    def safe_float(val):
        """安全转换为浮点数，处理inf和nan"""
        try:
            v = float(val)
            return v if np.isfinite(v) else 0.0
        except:
            return 0.0
    
    # 获取组合总价值曲线（每只股票的价值）
    portfolio_value = pf.value()
    
    # 计算组合总价值（所有股票价值之和）
    total_value = portfolio_value.sum(axis=1)
    
    # 计算总收益率：(最终价值 / 初始价值) - 1
    total_return = float((total_value.iloc[-1] / total_value.iloc[0]) - 1)
    
    # 计算年化收益率
    days = (close_df.index[-1] - close_df.index[0]).days
    annual_return = (1 + total_return) ** (365 / days) - 1 if days > 0 else 0
    
    # 计算夏普比率（风险调整后收益）
    daily_returns = total_value.pct_change().dropna()
    daily_returns_clean = daily_returns.replace([np.inf, -np.inf], np.nan).dropna()
    if len(daily_returns_clean) > 0:
        mean_ret = float(daily_returns_clean.mean())
        std_ret = float(daily_returns_clean.std())
        sharpe = mean_ret / std_ret * np.sqrt(252) if std_ret > 0 else 0.0
    else:
        sharpe = 0.0
    
    # 计算最大回撤（从最高点到最低点的跌幅）
    cummax = total_value.cummax()
    drawdown = (total_value - cummax) / cummax
    max_drawdown = float(drawdown.min())
    
    # 计算胜率
    records = pf.trades.records_readable
    if not records.empty:
        win_trades = (records['Return'] > 0).sum()
        total_trades = len(records)
        win_rate = win_trades / total_trades if total_trades > 0 else 0
    else:
        total_trades = 0
        win_rate = 0
    
    results = {
        'framework': 'VectorBT',
        'fast_window': fast_window,
        'slow_window': slow_window,
        'total_return': total_return,
        'annual_return': annual_return,
        'sharpe_ratio': sharpe,
        'max_drawdown': max_drawdown,
        'win_rate': win_rate,
        'total_trades': total_trades,
        'total_time': total_time,
        'signal_time': signal_time,
        'bt_time': bt_time,
    }
    
    logger.info(f"\nVectorBT 回测结果:")
    logger.info(f"  总收益率:      {total_return:.2%}")
    logger.info(f"  年化收益率:    {annual_return:.2%}")
    logger.info(f"  夏普比率:      {sharpe:.2f}")
    logger.info(f"  最大回撤:      {max_drawdown:.2%}")
    logger.info(f"  胜率:          {win_rate:.2%}")
    logger.info(f"  总交易次数:    {total_trades}")
    logger.info(f"  总耗时:        {total_time:.3f}秒")
    
    return results, pf


# ============================================================
# 第三部分：Qlib 实现
# ============================================================

def run_qlib_backtest(close_df, fast_window=5, slow_window=20, fees=0.001, slippage=0.001):
    """
    使用 Qlib 实现双均线策略
    
    参数:
        fees: 单边手续费 0.1%（买入或卖出时各收一次）
        slippage: 滑点 0.1%（实际成交价比理论价差一点）
    """
    logger.info(f"\n{'='*60}")
    logger.info("Qlib 双均线策略回测")
    logger.info(f"{'='*60}")
    logger.info(f"参数: 快线={fast_window}日, 慢线={slow_window}日")
    logger.info(f"费用: 手续费={fees:.1%} × 2(买卖), 滑点={slippage:.1%}")
    
    start_time = time.time()
    
    # 第1步：准备数据
    logger.info("准备数据...")
    data_start_time = time.time()
    data_time = time.time() - data_start_time
    logger.info(f"数据准备耗时: {data_time:.3f}秒")
    
    # 第2步：计算技术指标
    logger.info("计算技术指标...")
    indicator_start = time.time()
    
    results_list = []
    
    for ts_code in close_df.columns:
        stock_df = close_df[ts_code].dropna().to_frame('close')
        
        if len(stock_df) < slow_window:
            continue
        
        # 计算均线
        stock_df['fast_ma'] = stock_df['close'].rolling(window=fast_window).mean()
        stock_df['slow_ma'] = stock_df['close'].rolling(window=slow_window).mean()
        
        # 生成持仓信号
        stock_df['signal'] = 0
        stock_df.loc[stock_df['fast_ma'] > stock_df['slow_ma'], 'signal'] = 1
        stock_df.loc[stock_df['fast_ma'] < stock_df['slow_ma'], 'signal'] = 0
        
        # 计算每日收益率
        stock_df['daily_return'] = stock_df['close'].pct_change()
        
        # 计算换仓信号（信号发生变化时）
        stock_df['signal_change'] = stock_df['signal'].diff().abs()
        
        # 策略收益 = 信号 * 每日收益
        stock_df['strategy_return'] = stock_df['signal'].shift(1) * stock_df['daily_return']
        
        # 扣除交易成本：
        # - 每次换仓产生 fees + slippage 的成本
        # - 买入时扣一次，卖出时扣一次
        stock_df['cost'] = stock_df['signal_change'] * (fees + slippage)
        stock_df['strategy_return'] = stock_df['strategy_return'] - stock_df['cost']
        
        stock_df['ts_code'] = ts_code
        results_list.append(stock_df)
    
    indicator_time = time.time() - indicator_start
    logger.info(f"指标计算耗时: {indicator_time:.3f}秒")
    
    if not results_list:
        logger.error("没有足够的数据进行回测")
        return None, None
    
    # 第3步：合并结果
    all_results = pd.concat(results_list)
    
    # 第4步：计算组合绩效
    logger.info("计算组合绩效...")
    portfolio_start = time.time()
    
    # 按日期聚合所有股票的策略收益（等权配置）
    daily_strategy_returns = all_results.groupby(all_results.index)['strategy_return'].mean()
    daily_strategy_returns = daily_strategy_returns.dropna()
    
    # 计算累计收益曲线
    cumulative_returns = (1 + daily_strategy_returns).cumprod()
    
    # 计算总收益率
    total_return = float(cumulative_returns.iloc[-1] - 1) if len(cumulative_returns) > 0 else 0
    
    # 计算年化收益率
    days = len(daily_strategy_returns)
    annual_return = (1 + total_return) ** (252 / days) - 1 if days > 0 else 0
    
    # 计算夏普比率
    if len(daily_strategy_returns) > 0 and daily_strategy_returns.std() > 0:
        sharpe = float(daily_strategy_returns.mean() / daily_strategy_returns.std() * np.sqrt(252))
    else:
        sharpe = 0.0
    
    # 计算最大回撤
    cummax = cumulative_returns.cummax()
    drawdown = (cumulative_returns - cummax) / cummax
    max_drawdown = float(drawdown.min())
    
    # 计算胜率
    winning_days = (daily_strategy_returns > 0).sum()
    total_days = len(daily_strategy_returns)
    win_rate = winning_days / total_days if total_days > 0 else 0
    
    # 计算交易次数
    total_trades = 0
    for ts_code in close_df.columns:
        stock_signals = all_results[all_results['ts_code'] == ts_code]['signal']
        if len(stock_signals) > 1:
            changes = stock_signals.diff().fillna(0)
            total_trades += (changes != 0).sum()
    
    portfolio_time = time.time() - portfolio_start
    total_time = time.time() - start_time
    
    logger.info(f"组合绩效计算耗时: {portfolio_time:.3f}秒")
    logger.info(f"总耗时: {total_time:.3f}秒")
    
    results = {
        'framework': 'Qlib',
        'fast_window': fast_window,
        'slow_window': slow_window,
        'total_return': total_return,
        'annual_return': annual_return,
        'sharpe_ratio': sharpe,
        'max_drawdown': max_drawdown,
        'win_rate': win_rate,
        'total_trades': total_trades,
        'total_time': total_time,
        'data_time': data_time,
        'indicator_time': indicator_time,
        'portfolio_time': portfolio_time,
    }
    
    logger.info(f"\nQlib 回测结果:")
    logger.info(f"  总收益率:      {total_return:.2%}")
    logger.info(f"  年化收益率:    {annual_return:.2%}")
    logger.info(f"  夏普比率:      {sharpe:.2f}")
    logger.info(f"  最大回撤:      {max_drawdown:.2%}")
    logger.info(f"  胜率:          {win_rate:.2%}")
    logger.info(f"  总交易次数:    {total_trades}")
    logger.info(f"  总耗时:        {total_time:.3f}秒")
    
    return results, cumulative_returns


# ============================================================
# 第四部分：结果对比
# ============================================================

def compare_results(vbt_results, qlib_results):
    """
    对比两个框架的回测结果
    """
    logger.info(f"\n{'='*60}")
    logger.info("结果对比: VectorBT vs Qlib")
    logger.info(f"{'='*60}")
    
    # 创建对比表格
    metrics = [
        ('总收益率', 'total_return', '%'),
        ('年化收益率', 'annual_return', '%'),
        ('夏普比率', 'sharpe_ratio', 'f'),
        ('最大回撤', 'max_drawdown', '%'),
        ('胜率', 'win_rate', '%'),
        ('总交易次数', 'total_trades', 'd'),
        ('总耗时(秒)', 'total_time', 'f'),
    ]
    
    header = f"{'指标':<15} {'VectorBT':>15} {'Qlib':>15} {'差异':>15}"
    logger.info(f"\n{header}")
    logger.info("-" * 65)
    
    for name, key, fmt in metrics:
        vbt_val = vbt_results.get(key, 0)
        qlib_val = qlib_results.get(key, 0)
        
        if fmt == '%':
            diff = vbt_val - qlib_val
            line = f"{name:<15} {vbt_val:>14.2%} {qlib_val:>14.2%} {diff:>+14.2%}"
        elif fmt == 'd':
            diff = vbt_val - qlib_val
            line = f"{name:<15} {vbt_val:>15d} {qlib_val:>15d} {diff:>+15d}"
        else:
            diff = vbt_val - qlib_val
            line = f"{name:<15} {vbt_val:>15.2f} {qlib_val:>15.2f} {diff:>+15.2f}"
        
        logger.info(line)
    
    # 分析差异原因
    logger.info(f"\n{'='*60}")
    logger.info("差异分析")
    logger.info(f"{'='*60}")
    
    logger.info("""
1. 收益率差异：
   - VectorBT 使用完整的组合管理（仓位分配、手续费、滑点）
   - Qlib 使用简化的等权配置，不考虑交易成本
   - 因此 VectorBT 的收益率通常会低一些（因为扣除了费用）

2. 速度差异：
   - VectorBT 使用向量化操作，一次处理所有数据
   - Qlib 逐日遍历，速度较慢
   - VectorBT 通常快 10-100 倍

3. 功能差异：
   - VectorBT：专注回测，简单易用，适合快速验证策略
   - Qlib：完整平台，支持机器学习，适合深度研究

4. 适用场景：
   - 快速验证想法 → VectorBT
   - 机器学习研究 → Qlib
   - 生产环境回测 → 两者都可以，VectorBT 更快
""")
    
    return


# ============================================================
# 主程序
# ============================================================

def main():
    """
    主程序：运行双均线策略对比
    
    参数说明：
    ---------
    - 回测区间：2020-01-01 ~ 2026-06-30（约6.5年）
    - 快线窗口：5日（反映一周的趋势）
    - 慢线窗口：20日（反映一个月的趋势）
    - 股票池：成交量最大的50只股票
    - 初始资金：100万
    """
    logger.info("=" * 60)
    logger.info("双均线策略对比: VectorBT vs Qlib")
    logger.info("=" * 60)
    logger.info("策略: 5日均线上穿20日均线买入，下穿卖出")
    logger.info("股票池: 成交量最大的50只A股")
    logger.info("回测区间: 2020-01-01 ~ 2026-06-30")
    logger.info("")
    
    # 第1步：加载数据
    close_df, raw_df = load_data_from_db(
        start_date='2010-01-01',
        end_date='2026-06-30',
        top_n=5000
    )
    
    # 第2步：运行 VectorBT 回测（手续费0.1% + 滑点0.1%）
    vbt_results, vbt_pf = run_vectorbt_backtest(
        close_df, 
        fast_window=5, 
        slow_window=20,
        fees=0.001,
        slippage=0.001,
    )
    
    # 第3步：运行 Qlib 回测（相同费用参数）
    qlib_results, qlib_cumulative = run_qlib_backtest(
        close_df, 
        fast_window=5, 
        slow_window=20,
        fees=0.001,
        slippage=0.001,
    )
    
    # 第4步：对比结果
    if vbt_results and qlib_results:
        compare_results(vbt_results, qlib_results)
    
    logger.info("\n" + "=" * 60)
    logger.info("回测完成！")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
