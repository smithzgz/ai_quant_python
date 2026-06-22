# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.strategies.macd import MACD
from backtest.strategies.ma_cross import MACross
from backtest.strategies.momentum import Momentum
from backtest.engine.vbt_engine import VBTEngine
from backtest.storage.queries import execute_query
from data.database.connection import SessionLocal
from data.database.models import BacktestRun

def run_multi_strategy_backtest():
    """运行多策略回测任务"""

    print('=== 运行多策略回测任务 ===')
    print()

    bt = VBTEngine()

    # 测试策略组合
    strategies = [
        {
            'name': 'macd',
            'config': {'fast_window': 12, 'slow_window': 26, 'signal_window': 9}
        },
        {
            'name': 'ma_cross',
            'config': {'fast_window': 5, 'slow_window': 20}
        },
        {
            'name': 'momentum',
            'config': {'lookback_window': 20, 'threshold': 0.02}
        }
    ]

    # 测试标的
    symbols = ['000001.SZ', '600036.SH', '600519.SH', '000002.SZ', '601318.SH']
    print(f'测试标的: {symbols}')
    print(f'回测周期: 2025-01-01 ~ 2026-06-15')
    print(f'初始资金: 100,000元')
    print()

    results = {}
    for strat in strategies:
        print(f'--- 运行 {strat["name"].upper()} 策略 ---')
        try:
            run_id = bt.run(
                strategy_name=strat['name'],
                symbols=symbols,
                start='2025-01-01',
                end='2026-06-15',
                strategy_config=strat['config'],
                init_cash=100000.0,
            )
            results[strat['name']] = run_id
            print(f'  回测完成! run_id={run_id}')
        except Exception as e:
            print(f'  回测失败: {e}')
            import traceback
            traceback.print_exc()
        print()

    # 显示结果
    print('=== 回测结果汇总 ===')
    session = SessionLocal()
    for name, run_id in results.items():
        print(f'\n{name.upper()} 策略 (run_id={run_id}):')
        try:
            run = session.query(BacktestRun).get(run_id)
            if run:
                print(f'  总收益率: {run.total_return:.2f}%')
                print(f'  最大回撤: {run.max_drawdown:.2f}%')
                print(f'  Sharpe: {run.sharpe_ratio:.2f}')
                print(f'  总交易: {run.total_trades}')
                print(f'  最终资产: {run.final_value:,.2f}元')
                print(f'  胜率: {run.win_rate:.2f}%')
            else:
                print(f'  未找到回测记录')
        except Exception as e:
            print(f'  获取结果失败: {e}')

    session.close()
    return results

if __name__ == "__main__":
    run_multi_strategy_backtest()