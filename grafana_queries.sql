-- Grafana 仪表板 SQL 查询模板
-- 将这些查询复制到 Grafana 面板中，替换变量 $run_id

-- ============================================================
-- 1. 回测运行概览面板 (Table/Stat)
-- ============================================================

-- 回测运行列表
SELECT
    id,
    strategy_name,
    start_date,
    end_date,
    init_cash,
    final_value,
    ROUND(total_return, 2) as total_return_pct,
    ROUND(max_drawdown, 2) as max_drawdown_pct,
    ROUND(sharpe_ratio, 2) as sharpe_ratio,
    ROUND(win_rate, 2) as win_rate_pct,
    total_trades,
    created_at
FROM backtest_runs
ORDER BY created_at DESC
LIMIT 10;

-- 策略收益对比 (Time Series / Bar)
SELECT
    created_at as time,
    strategy_name,
    total_return as total_return_pct
FROM backtest_runs
WHERE created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at;

-- 最佳策略 (Stat)
SELECT
    strategy_name,
    ROUND(MAX(total_return), 2) as best_return
FROM backtest_runs
GROUP BY strategy_name
ORDER BY best_return DESC
LIMIT 1;

-- ============================================================
-- 2. 净值曲线面板 (Time Series Line)
-- ============================================================

-- 单次回测净值曲线
SELECT
    timestamp as time,
    equity_value
FROM equity_curves
WHERE run_id = $run_id
ORDER BY timestamp;

-- 多策略净值曲线对比
SELECT
    ec.timestamp as time,
    ec.equity_value,
    br.strategy_name,
    br.id as run_id
FROM equity_curves ec
JOIN backtest_runs br ON ec.run_id = br.id
WHERE br.created_at > NOW() - INTERVAL '7 days'
ORDER BY ec.timestamp;

-- ============================================================
-- 3. 回撤分析面板 (Time Series)
-- ============================================================

-- 单次回测回撤曲线
SELECT
    timestamp as time,
    drawdown as max_drawdown_pct
FROM equity_curves
WHERE run_id = $run_id
ORDER BY timestamp;

-- ============================================================
-- 4. 交易分析面板
-- ============================================================

-- 交易列表 (Table)
SELECT
    run_id,
    symbol,
    direction,
    entry_time,
    exit_time,
    ROUND(entry_price, 2) as entry_price,
    ROUND(exit_price, 2) as exit_price,
    ROUND(size, 2) as size,
    ROUND(pnl, 2) as pnl,
    ROUND(return_pct, 2) as return_pct,
    ROUND(fees, 2) as fees,
    duration_bars
FROM trade_records
WHERE run_id = $run_id
ORDER BY entry_time DESC;

-- 交易盈亏分布 (Pie Chart)
SELECT
    CASE WHEN pnl > 0 THEN 'Win' ELSE 'Loss' END as result,
    COUNT(*) as trade_count,
    ROUND(SUM(pnl), 2) as total_pnl
FROM trade_records
WHERE run_id = $run_id
GROUP BY result;

-- 标的盈亏分布 (Bar Chart)
SELECT
    symbol,
    COUNT(*) as trade_count,
    ROUND(SUM(pnl), 2) as total_pnl,
    ROUND(AVG(pnl), 2) as avg_pnl
FROM trade_records
WHERE run_id = $run_id
GROUP BY symbol
ORDER BY total_pnl DESC;

-- 日均收益分布 (Time Series / Bar)
SELECT
    timestamp::date as time,
    ROUND(AVG(daily_return), 2) as avg_return
FROM equity_curves
WHERE run_id = $run_id
GROUP BY timestamp::date
ORDER BY time;

-- ============================================================
-- 5. 数据质量监控面板
-- ============================================================

-- 最新数据同步状态
SELECT
    table_name,
    last_sync_date,
    updated_at
FROM sync_checkpoint
ORDER BY updated_at DESC
LIMIT 10;

-- 数据质量检查结果
SELECT
    table_name,
    rule_name,
    status,
    total_rows,
    issue_count,
    checked_at
FROM data_quality_log
WHERE checked_at > NOW() - INTERVAL '7 days'
ORDER BY checked_at DESC;

-- ============================================================
-- 6. 数据概览面板
-- ============================================================

-- 数据表行数统计
SELECT 'daily' as table_name, COUNT(*) as row_count FROM daily
UNION ALL
SELECT 'daily_basic', COUNT(*) FROM daily_basic
UNION ALL
SELECT 'adj_factor', COUNT(*) FROM adj_factor
UNION ALL
SELECT 'stock_basic', COUNT(*) FROM stock_basic;

-- 日线数据时间范围
SELECT
    MIN(trade_date) as min_date,
    MAX(trade_date) as max_date,
    COUNT(DISTINCT trade_date) as trading_days
FROM daily;

-- 最新交易日数据
SELECT
    trade_date::date as time,
    COUNT(*) as stock_count,
    ROUND(AVG(close), 2) as avg_close,
    ROUND(AVG(pct_chg), 2) as avg_pct_chg
FROM daily
WHERE trade_date = (SELECT MAX(trade_date) FROM daily)
GROUP BY trade_date;

-- ============================================================
-- 7. 高级分析面板
-- ============================================================

-- 按月统计收益 (Bar Chart)
SELECT
    EXTRACT(YEAR FROM timestamp) as year,
    EXTRACT(MONTH FROM timestamp) as month,
    ROUND(SUM(daily_return), 2) as monthly_return
FROM equity_curves
WHERE run_id = $run_id
GROUP BY year, month
ORDER BY year, month;

-- 策略性能对比 (Bar Chart)
SELECT
    strategy_name,
    COUNT(*) as runs,
    ROUND(AVG(total_return), 2) as avg_return,
    ROUND(AVG(max_drawdown), 2) as avg_drawdown,
    ROUND(AVG(sharpe_ratio), 2) as avg_sharpe,
    ROUND(AVG(win_rate), 2) as avg_win_rate
FROM backtest_runs
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY strategy_name;

-- 持仓周期分析 (Histogram)
SELECT
    CASE
        WHEN duration_bars <= 5 THEN '1-5天'
        WHEN duration_bars <= 10 THEN '6-10天'
        WHEN duration_bars <= 20 THEN '11-20天'
        ELSE '>20天'
    END as holding_period,
    COUNT(*) as trade_count,
    ROUND(AVG(pnl), 2) as avg_pnl,
    ROUND(AVG(return_pct), 2) as avg_return
FROM trade_records
WHERE run_id = $run_id AND duration_bars IS NOT NULL
GROUP BY holding_period
ORDER BY holding_period;