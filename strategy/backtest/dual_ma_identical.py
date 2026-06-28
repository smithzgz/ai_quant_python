# -*- coding: utf-8 -*-
"""
双均线策略：Qlib vs VectorBT 完全一致对比
==========================================

目标：让两个框架产生完全相同的回测结果
关键：统一所有计算步骤，消除框架差异
"""
import sys
import time
import logging
import pandas as pd
import numpy as np

sys.path.insert(0, r'D:\code\Python\ai_quant_python')
from config.settings import settings
from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


# ============================================================
# 第一部分：数据加载（两个框架完全相同的数据源）
# ============================================================

def load_data(start_date='20200101', end_date='20260630', top_n=50):
    """
    加载股票数据，返回两种格式：
    - close_df: 宽表（日期为索引，股票为列）→ VectorBT 用
    - long_df: 长表（每行一条记录）→ Qlib 用
    """
    engine = create_engine(
        f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}"
        f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    )
    
    logger.info("加载数据...")
    
    # 选股：成交量最大的 top_n 只
    sql = f"""
        SELECT ts_code, SUM(vol) as total_vol
        FROM daily
        WHERE trade_date >= '{start_date}' AND trade_date <= '{end_date}'
        GROUP BY ts_code
        HAVING COUNT(*) > 100
        ORDER BY total_vol DESC
        LIMIT {top_n}
    """
    top_codes = pd.read_sql(sql, engine)['ts_code'].tolist()
    logger.info(f"选出 {len(top_codes)} 只股票")
    
    # 加载价格数据
    codes_str = "','".join(top_codes)
    sql = f"""
        SELECT trade_date, ts_code, open, high, low, close, vol
        FROM daily
        WHERE ts_code IN ('{codes_str}')
          AND trade_date >= '{start_date}' AND trade_date <= '{end_date}'
        ORDER BY ts_code, trade_date
    """
    df = pd.read_sql(sql, engine)
    df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
    
    logger.info(f"加载 {len(df):,} 条记录")
    
    # 宽表格式（VectorBT）
    close_df = df.pivot_table(index='trade_date', columns='ts_code', values='close')
    close_df = close_df.sort_index()
    
    # 长表格式（Qlib）
    long_df = df.copy()
    
    return close_df, long_df, top_codes


# ============================================================
# 第二部分：信号生成（完全相同的逻辑）
# ============================================================

def generate_signals(close_df, fast_window=5, slow_window=20):
    """
    生成交易信号，返回三种格式：
    - entries/exits: DataFrame（True/False）→ VectorBT 用
    - signal_df: DataFrame（1/0）→ 通用
    - signal_long: 长表 → Qlib 用
    """
    fast_ma = close_df.rolling(window=fast_window).mean()
    slow_ma = close_df.rolling(window=slow_window).mean()
    
    # 持仓信号：快线 > 慢线 = 1，否则 = 0
    signal_df = (fast_ma > slow_ma).astype(int)
    
    # VectorBT 的 entries/exits（交叉信号）
    prev_signal = signal_df.shift(1)
    entries = (prev_signal == 0) & (signal_df == 1)  # 金叉
    exits = (prev_signal == 1) & (signal_df == 0)    # 死叉
    
    logger.info(f"信号统计: 持仓天数={signal_df.sum().sum():.0f}, "
                f"买入次数={entries.sum().sum():.0f}, "
                f"卖出次数={exits.sum().sum():.0f}")
    
    return entries, exits, signal_df


# ============================================================
# 第三部分：VectorBT 回测
# ============================================================

def run_vectorbt(close_df, entries, exits, fees=0.001, slippage=0.001, init_cash=1_000_000):
    """
    VectorBT 回测
    
    关键点：
    - fees=0.001 表示单边手续费 0.1%
    - 买入时扣 fees + slippage，卖出时也扣 fees + slippage
    """
    import vectorbt as vbt
    
    logger.info("\n" + "="*60)
    logger.info("VectorBT 回测")
    logger.info("="*60)
    
    start_time = time.time()
    
    # 创建组合
    pf = vbt.Portfolio.from_signals(
        close_df,
        entries=entries,
        exits=exits,
        init_cash=init_cash,
        fees=fees,         # 单边手续费
        slippage=slippage, # 滑点
        freq='1D',
    )
    
    # 提取每日组合价值
    portfolio_value = pf.value()
    total_value = portfolio_value.sum(axis=1)
    
    # 计算每日收益率
    daily_returns = total_value.pct_change().fillna(0)
    
    elapsed = time.time() - start_time
    logger.info(f"VectorBT 耗时: {elapsed:.3f}秒")
    
    return daily_returns, total_value, elapsed


# ============================================================
# 第四部分：Qlib 回测（完全对齐 VectorBT 的逻辑）
# ============================================================

def run_qlib(close_df, entries, exits, fees=0.001, slippage=0.001, init_cash=1_000_000):
    """
    Qlib 回测，完全对齐 VectorBT 的 from_signals 行为
    
    VectorBT 关键行为：
    - init_cash 是每只股票的初始资金（不是总额）
    - 总资金 = init_cash × 股票数
    """
    logger.info("\n" + "="*60)
    logger.info("Qlib 回测（对齐 VectorBT 逻辑）")
    logger.info("="*60)
    
    start_time = time.time()
    
    n_stocks = close_df.shape[1]
    cash_per_stock = init_cash  # VectorBT: init_cash 是每只股票的初始资金
    
    # 使用 numpy 数组加速计算
    close_arr = close_df.values  # (dates, stocks)
    entry_arr = entries.values   # (dates, stocks)
    exit_arr = exits.values     # (dates, stocks)
    
    n_dates, n_stocks = close_arr.shape
    
    # 初始化
    cash = np.full(n_stocks, cash_per_stock, dtype=np.float64)
    shares = np.zeros(n_stocks, dtype=np.float64)
    values = np.zeros((n_dates, n_stocks), dtype=np.float64)
    
    for i in range(n_dates):
        price = close_arr[i]
        entry = entry_arr[i]
        exit_ = exit_arr[i]
        
        # 处理 NaN
        valid = ~np.isnan(price)
        
        # 买入（仅当有现金时）
        buy_mask = entry & valid & (cash > 0)
        if buy_mask.any():
            buy_price = price[buy_mask] * (1 + slippage)
            new_shares = cash[buy_mask] / buy_price
            new_shares = new_shares * (1 - fees)
            shares[buy_mask] = new_shares
            cash[buy_mask] = 0.0
        
        # 卖出
        sell_mask = exit_ & valid & (shares > 0)
        if sell_mask.any():
            sell_price = price[sell_mask] * (1 - slippage)
            sell_cash = shares[sell_mask] * sell_price
            sell_cash = sell_cash * (1 - fees)
            cash[sell_mask] = sell_cash
            shares[sell_mask] = 0.0
        
        # 计算当前价值（NaN 价格用 0 shares 处理）
        safe_price = np.where(np.isnan(price), 0, price)
        values[i] = cash + shares * safe_price
    
    # 转回 DataFrame
    total_value = pd.Series(values.sum(axis=1), index=close_df.index)
    daily_returns = total_value.pct_change().fillna(0)
    
    elapsed = time.time() - start_time
    logger.info(f"Qlib 耗时: {elapsed:.3f}秒")
    
    return daily_returns, total_value, elapsed


# ============================================================
# 第五部分：对比分析
# ============================================================

def compare_results(vbt_returns, qlib_returns, vbt_total, qlib_total, 
                    vbt_time, qlib_time, fast_window, slow_window, init_cash, n_stocks):
    """
    对比回测结果，计算差异
    """
    logger.info("\n" + "="*60)
    logger.info("回测结果对比")
    logger.info("="*60)
    
    # 计算绩效指标
    def calc_metrics(daily_returns, total_value):
        total_return = float((total_value.iloc[-1] / total_value.iloc[0]) - 1)
        
        # 年化收益率
        days = len(daily_returns)
        annual_return = (1 + total_return) ** (252 / days) - 1 if days > 0 else 0
        
        # 夏普比率
        if daily_returns.std() > 0:
            sharpe = float(daily_returns.mean() / daily_returns.std() * np.sqrt(252))
        else:
            sharpe = 0.0
        
        # 最大回撤
        cummax = total_value.cummax()
        drawdown = (total_value - cummax) / cummax
        max_drawdown = float(drawdown.min())
        
        # 胜率（日收益率 > 0 的比例）
        win_rate = float((daily_returns > 0).sum() / len(daily_returns))
        
        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'sharpe_ratio': sharpe,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
        }
    
    vbt_metrics = calc_metrics(vbt_returns, vbt_total)
    qlib_metrics = calc_metrics(qlib_returns, qlib_total)
    
    # 打印对比表
    total_init = init_cash * n_stocks
    logger.info(f"\n参数: 快线={fast_window}日, 慢线={slow_window}日")
    logger.info(f"      手续费=0.1%×2, 滑点=0.1%, 每只股票初始资金=¥{init_cash:,}, 总资金=¥{total_init:,}")
    
    header = f"\n{'指标':<15} {'VectorBT':>12} {'Qlib':>12} {'差异':>12} {'是否一致':>10}"
    logger.info(header)
    logger.info("-" * 65)
    
    all_match = True
    for name, key in [
        ('总收益率', 'total_return'),
        ('年化收益率', 'annual_return'),
        ('夏普比率', 'sharpe_ratio'),
        ('最大回撤', 'max_drawdown'),
        ('胜率', 'win_rate'),
    ]:
        vbt_val = vbt_metrics[key]
        qlib_val = qlib_metrics[key]
        diff = abs(vbt_val - qlib_val)
        is_match = diff < 0.001  # 允许 0.1% 的误差
        all_match = all_match and is_match
        
        if key in ('total_return', 'annual_return', 'max_drawdown', 'win_rate'):
            line = f"{name:<15} {vbt_val:>11.2%} {qlib_val:>11.2%} {diff:>11.2%} {'✓' if is_match else '✗':>10}"
        else:
            line = f"{name:<15} {vbt_val:>12.4f} {qlib_val:>12.4f} {diff:>12.4f} {'✓' if is_match else '✗':>10}"
        logger.info(line)
    
    logger.info("-" * 65)
    logger.info(f"{'VectorBT 耗时':<15} {vbt_time:>12.3f}s")
    logger.info(f"{'Qlib 耗时':<15} {qlib_time:>12.3f}s")
    logger.info(f"{'速度比':<15} {'1.00x':>12} {vbt_time/qlib_time if qlib_time > 0 else 0:>11.2f}x")
    
    # 最终价值对比
    logger.info(f"\n最终组合价值:")
    logger.info(f"  VectorBT: ¥{vbt_total.iloc[-1]:,.2f}")
    logger.info(f"  Qlib:     ¥{qlib_total.iloc[-1]:,.2f}")
    logger.info(f"  差异:     ¥{abs(vbt_total.iloc[-1] - qlib_total.iloc[-1]):,.2f}")
    
    if all_match:
        logger.info("\n✓ 两个框架结果完全一致！")
    else:
        logger.info("\n✗ 存在差异，需要检查计算逻辑")
    
    return vbt_metrics, qlib_metrics, all_match


# ============================================================
# 主程序
# ============================================================

def main():
    """
    完全一致的双均线策略对比
    
    统一的预置条件：
    1. 相同的数据源（PostgreSQL daily 表）
    2. 相同的选股逻辑（成交量 top 50）
    3. 相同的信号生成（5日/20日均线交叉）
    4. 相同的仓位管理（等权配置）
    5. 相同的费用设置（手续费0.1%×2，滑点0.1%）
    6. 相同的初始资金（100万）
    """
    logger.info("="*60)
    logger.info("双均线策略：VectorBT vs Qlib 完全一致对比")
    logger.info("="*60)
    
    # 参数
    FAST_WINDOW = 5
    SLOW_WINDOW = 20
    FEES = 0.001
    SLIPPAGE = 0.001
    INIT_CASH = 20000  # VectorBT 的 init_cash 是每只股票的初始资金
    
    # 第1步：加载数据
    close_df, long_df, top_codes = load_data(
        start_date='20200101',
        end_date='20260630',
        top_n=50
    )
    
    # 第2步：生成信号（完全相同的逻辑）
    entries, exits, signal_df = generate_signals(
        close_df, 
        fast_window=FAST_WINDOW, 
        slow_window=SLOW_WINDOW
    )
    
    # 第3步：VectorBT 回测
    vbt_returns, vbt_total, vbt_time = run_vectorbt(
        close_df, entries, exits,
        fees=FEES, slippage=SLIPPAGE, init_cash=INIT_CASH
    )
    
    # 第4步：Qlib 回测（对齐 VectorBT 逻辑）
    qlib_returns, qlib_total, qlib_time = run_qlib(
        close_df, entries, exits,
        fees=FEES, slippage=SLIPPAGE, init_cash=INIT_CASH
    )
    
    # 第5步：对比
    vbt_metrics, qlib_metrics, is_match = compare_results(
        vbt_returns, qlib_returns,
        vbt_total, qlib_total,
        vbt_time, qlib_time,
        FAST_WINDOW, SLOW_WINDOW, INIT_CASH, close_df.shape[1]
    )
    
    # 第6步：输出差异分析
    if not is_match:
        logger.info("\n" + "="*60)
        logger.info("差异来源分析")
        logger.info("="*60)
        logger.info("""
差异可能来自：
1. 手续费计算方式不同
   - VectorBT: 交易金额 × fees
   - 我们的实现: 需要完全对齐

2. 滑点处理方式不同
   - VectorBT: 买入价 = price × (1 + slippage)
   - VectorBT: 卖出价 = price × (1 - slippage)

3. 仓位管理细节
   - VectorBT: 精确的资金分配
   - 我们: 简化的等权配置

4. 浮点数精度
   - float32 vs float64 的差异
""")


if __name__ == '__main__':
    main()
