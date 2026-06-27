# -*- coding: utf-8 -*-
"""
研报评级变化策略回测脚本
Run after JLP sync completes.
"""
import sys
import logging

sys.path.insert(0, r'D:\code\Python\ai_quant_python')

from config.settings import settings
from strategy.jlp_sentiment import run_backtest

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def main():
    import psycopg2
    
    logger.info("Connecting to database...")
    conn = psycopg2.connect(
        host=settings.DB_HOST, port=settings.DB_PORT,
        user=settings.DB_USER, password=settings.DB_PASSWORD,
        database=settings.DB_NAME
    )
    
    try:
        # Check data availability first
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT ts_code) FROM sohu_jlp")
        total, stocks = cur.fetchone()
        logger.info(f"JLP data: {total} records, {stocks} stocks")
        
        cur.execute("""
            SELECT COUNT(*) FROM sohu_jlp 
            WHERE target_space IS NOT NULL AND target_space != '--' AND target_space != ''
        """)
        valid = cur.fetchone()[0]
        logger.info(f"Records with valid target_space: {valid}")
        
        if valid < 100:
            logger.warning("Not enough data for backtest. Wait for sync to complete.")
            return
        
        # Run backtest with different thresholds
        thresholds = [5, 10, 15, 20]
        results = []
        
        for threshold in thresholds:
            logger.info(f"\n{'='*60}")
            logger.info(f"Running backtest with threshold={threshold}")
            logger.info(f"{'='*60}")
            
            result = run_backtest(
                conn,
                threshold=threshold,
                start_date='2022-04-01',
                end_date='2026-06-30',
            )
            results.append(result)
            
            if 'error' in result:
                logger.error(f"Error: {result['error']}")
                continue
            
            logger.info(f"\nResults for threshold={threshold}:")
            logger.info(f"  Total Return:    {result['total_return']:.2%}")
            logger.info(f"  Sharpe Ratio:    {result['sharpe_ratio']:.2f}")
            logger.info(f"  Max Drawdown:    {result['max_drawdown']:.2%}")
            logger.info(f"  Win Rate:        {result['win_rate']:.2%}")
            logger.info(f"  Num Trades:      {result['num_trades']}")
            logger.info(f"  Stocks Traded:   {result['stocks_traded']}")
            logger.info(f"  Signal Count:    {result['signal_count']}")
        
        # Summary
        logger.info(f"\n{'='*60}")
        logger.info("SUMMARY")
        logger.info(f"{'='*60}")
        
        valid_results = [r for r in results if 'error' not in r]
        if valid_results:
            best = max(valid_results, key=lambda x: x['total_return'])
            logger.info(f"\nBest threshold: {best['threshold']}")
            logger.info(f"  Total Return:  {best['total_return']:.2%}")
            logger.info(f"  Sharpe Ratio:  {best['sharpe_ratio']:.2f}")
            logger.info(f"  Max Drawdown:  {best['max_drawdown']:.2%}")
            logger.info(f"  Win Rate:      {best['win_rate']:.2%}")
            logger.info(f"  Num Trades:    {best['num_trades']}")
        
        # Show per-stock returns for best result
        if valid_results and best.get('stock_returns'):
            logger.info(f"\nTop 10 stocks by return:")
            sorted_stocks = sorted(best['stock_returns'].items(), 
                                   key=lambda x: x[1]['return'], reverse=True)
            for stock, stats in sorted_stocks[:10]:
                logger.info(f"  {stock}: return={stats['return']:.2%}, trades={stats['trades']}")
    
    finally:
        conn.close()
    
    logger.info("\nBacktest completed.")


if __name__ == '__main__':
    main()
