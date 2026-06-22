# -*- coding: utf-8 -*-
"""
MACD 端到端测试脚本

验证整体流程: 初始化DB → 同步数据 → 质检 → MACD回测 → 查看结果

使用前:
1. 确保 PostgreSQL 已启动，且 quant_db 数据库已创建
2. 确保 .env 文件中 DB 配置正确
3. pip install -r requirements.txt

运行:
    python test_e2e_macd.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta
from data.database.connection import engine, SessionLocal
from data.database.timescale import init_all_tables
from data.database.models import BacktestRun, TradeRecord, EquityCurve, SyncCheckpoint
from data.sync.engine import SyncEngine
from data.quality.checker import QualityChecker
from backtest.engine.vbt_engine import VBTEngine
from backtest.strategies.registry import StrategyRegistry
from backtest.broker.a_share import AShareBroker
from utils.logger import get_logger

logger = get_logger("e2e_test")

SEPARATOR = "=" * 60


def step(name: str):
    logger.info(f"\n{SEPARATOR}\n  STEP: {name}\n{SEPARATOR}")


def test_db_connection():
    step("1. 测试数据库连接")
    try:
        with engine.connect() as conn:
            result = conn.execute("SELECT version()")
            version = result.scalar()
            logger.info(f"PostgreSQL version: {version}")
            return True
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        logger.error("请确保 PostgreSQL 已启动，且 quant_db 数据库已创建")
        logger.error("创建数据库命令: createdb -U quant quant_db")
        return False


def test_init_db():
    step("2. 初始化数据库表")
    try:
        init_all_tables()
        with engine.connect() as conn:
            from sqlalchemy import text
            tables = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' ORDER BY table_name"
            )).fetchall()
            table_names = [t[0] for t in tables]
            logger.info(f"已创建 {len(table_names)} 个表: {', '.join(table_names)}")

            expected = ["trade_cal", "stock_basic", "daily", "daily_basic",
                        "adj_factor", "fund_basic", "fund_daily",
                        "backtest_runs", "trade_records", "equity_curves",
                        "position_snapshots", "sync_log", "sync_checkpoint",
                        "data_quality_log"]
            missing = [t for t in expected if t not in table_names]
            if missing:
                logger.warning(f"缺少表: {missing}")
            else:
                logger.info("所有预期表已创建 ✓")
        return True
    except Exception as e:
        logger.error(f"初始化数据库失败: {e}")
        return False


def test_sync():
    step("3. 同步数据 (trade_cal → stock_basic → daily → daily_basic → adj_factor)")
    engine = SyncEngine()

    sync_tables = ["trade_cal", "stock_basic"]

    for table_name in sync_tables:
        try:
            logger.info(f"同步 {table_name}...")
            engine.sync(table_name=table_name)
        except Exception as e:
            logger.error(f"同步 {table_name} 失败: {e}")
            if table_name == "trade_cal":
                logger.error("trade_cal 是前置依赖，同步失败无法继续")
                return False

    try:
        logger.info("同步 daily (最近3个交易日)...")
        from datetime import date
        end_date = date.today()
        from sqlalchemy import text
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT cal_date FROM trade_cal WHERE is_open = 1 "
                "AND cal_date <= CURRENT_DATE ORDER BY cal_date DESC LIMIT 3"
            )).fetchall()

        if result:
            start_date = result[-1][0]
            from data.sync.tushare_client import TushareClient
            import pandas as pd

            client = TushareClient(sleep_interval=0.5)
            for row in result:
                td = row[0]
                td_str = td.strftime("%Y%m%d") if hasattr(td, 'strftime') else str(td)

                for api_name, table_name in [("daily", "daily"), ("daily_basic", "daily_basic"), ("adj_factor", "adj_factor")]:
                    try:
                        if api_name == "daily_basic":
                            data = client.call(api_name, trade_date=td_str,
                                               fields="ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,free_share,total_mv,circ_mv")
                        elif api_name == "adj_factor":
                            data = client.call(api_name, trade_date=td_str,
                                               fields="ts_code,trade_date,adj_factor")
                        else:
                            data = client.call(api_name, trade_date=td_str,
                                               fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount")

                        if data is not None and not data.empty:
                            from data.sync.engine import _convert_dates
                            from config.data_sync_config import DATA_SYNC_TASKS
                            cfg = DATA_SYNC_TASKS[table_name]
                            data = _convert_dates(data, cfg)
                            engine._write_df(table_name, data, cfg)
                            logger.info(f"  {table_name} {td_str}: {len(data)} rows")
                    except Exception as e:
                        logger.warning(f"  {table_name} {td_str}: {e}")

                engine._update_checkpoint("daily", td)
                engine._update_checkpoint("daily_basic", td)
                engine._update_checkpoint("adj_factor", td)
    except Exception as e:
        logger.error(f"同步daily失败: {e}")

    with engine.connect() as conn:
        for table_name in ["trade_cal", "stock_basic", "daily", "daily_basic", "adj_factor"]:
            try:
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
                logger.info(f"  {table_name}: {count} rows")
            except Exception:
                logger.info(f"  {table_name}: table not found or empty")

    return True


def test_quality():
    step("4. 数据质量检查")
    checker = QualityChecker()
    for table_name in ["daily", "daily_basic", "adj_factor"]:
        try:
            results = checker.check_table(table_name)
            for r in results:
                logger.info(f"  {r['rule_name']}: {r['status']} ({r['issue_count']} issues)")
        except Exception as e:
            logger.warning(f"质检 {table_name} 失败: {e}")
    return True


def test_backtest():
    step("5. MACD 策略回测")

    with engine.connect() as conn:
        from sqlalchemy import text
        result = conn.execute(text(
            "SELECT DISTINCT ts_code FROM daily ORDER BY ts_code LIMIT 5"
        )).fetchall()

    if not result:
        logger.error("daily 表无数据，无法回测")
        return False

    symbols = [r[0] for r in result]
    logger.info(f"回测标的: {symbols}")

    with engine.connect() as conn:
        from sqlalchemy import text
        date_range = conn.execute(text(
            "SELECT MIN(trade_date), MAX(trade_date) FROM daily"
        )).fetchone()
    logger.info(f"数据范围: {date_range[0]} ~ {date_range[1]}")

    start = str(date_range[0])
    end = str(date_range[1])

    bt_engine = VBTEngine()
    try:
        run_id = bt_engine.run(
            strategy_name="macd",
            symbols=symbols,
            start=start,
            end=end,
            strategy_config={"fast_period": 12, "slow_period": 26, "signal_period": 9},
            init_cash=100000.0,
        )
        logger.info(f"回测完成! run_id = {run_id}")
        return run_id
    except Exception as e:
        logger.error(f"回测失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_verify_results(run_id):
    step("6. 验证回测结果")
    if not run_id:
        logger.error("无 run_id，跳过验证")
        return False

    session = SessionLocal()
    try:
        run = session.query(BacktestRun).get(run_id)
        if not run:
            logger.error(f"Run {run_id} 不存在")
            return False

        logger.info(f"策略: {run.strategy_name}")
        logger.info(f"总收益率: {run.total_return:.2f}%")
        logger.info(f"年化收益: {run.annual_return:.2f}%" if run.annual_return else "年化收益: N/A")
        logger.info(f"最大回撤: {run.max_drawdown:.2f}%")
        logger.info(f"Sharpe: {run.sharpe_ratio:.2f}" if run.sharpe_ratio else "Sharpe: N/A")
        logger.info(f"总交易次数: {run.total_trades}")
        logger.info(f"最终资产: {run.final_value:.2f}")

        trades = session.query(TradeRecord).filter_by(run_id=run_id).all()
        logger.info(f"\n交易记录 (共 {len(trades)} 笔):")
        for t in trades[:10]:
            logger.info(
                f"  {t.symbol} | {t.direction} | "
                f"entry={t.entry_price:.2f} exit={t.exit_price:.2f} | "
                f"pnl={t.pnl:.2f} fees={t.fees:.2f}"
            )
        if len(trades) > 10:
            logger.info(f"  ... 还有 {len(trades) - 10} 笔交易")

        equity_count = session.query(EquityCurve).filter_by(run_id=run_id).count()
        logger.info(f"\n净值曲线数据点: {equity_count}")

        return True
    finally:
        session.close()


def test_broker_fees():
    step("7. 验证A股交易费用计算")
    buy_amount = 10000.0
    sell_amount = 11000.0

    commission_buy = AShareBroker.calc_commission(buy_amount)
    commission_sell = AShareBroker.calc_commission(sell_amount)
    stamp_duty = AShareBroker.calc_stamp_duty(sell_amount)
    total = AShareBroker.calc_total_fees(buy_amount, sell_amount)

    logger.info(f"买入金额: {buy_amount}, 佣金: {commission_buy:.2f}")
    logger.info(f"卖出金额: {sell_amount}, 佣金: {commission_sell:.2f}, 印花税: {stamp_duty:.2f}")
    logger.info(f"总费用: {total:.2f}")

    assert commission_buy == 5.0, f"最低佣金应为5元，实际{commission_buy}"
    assert stamp_duty == sell_amount * 0.0005, "印花税计算错误"
    assert AShareBroker.round_to_lot(250) == 200, "整手取整错误"

    logger.info("A股交易费用计算验证通过 ✓")
    return True


def test_strategies():
    step("8. 验证策略注册")
    strategies = StrategyRegistry.list_all()
    logger.info(f"已注册策略: {strategies}")

    assert "macd" in strategies, "MACD策略未注册"
    assert "ma_cross" in strategies, "均线交叉策略未注册"
    assert "momentum" in strategies, "动量策略未注册"

    macd = StrategyRegistry.get("macd")
    assert macd.name == "macd"
    assert macd.get_default_config() == {"fast_period": 12, "slow_period": 26, "signal_period": 9}

    logger.info("策略注册验证通过 ✓")
    return True


def main():
    logger.info("MACD 端到端测试开始\n")

    results = {}

    results["db_connection"] = test_db_connection()
    if not results["db_connection"]:
        logger.error("数据库连接失败，测试终止。请先安装并启动 PostgreSQL。")
        return

    results["init_db"] = test_init_db()
    if not results["init_db"]:
        logger.error("数据库初始化失败，测试终止。")
        return

    results["sync"] = test_sync()

    results["quality"] = test_quality()

    results["broker"] = test_broker_fees()

    results["strategies"] = test_strategies()

    run_id = test_backtest()
    results["backtest"] = run_id is not False

    if results["backtest"]:
        results["verify"] = test_verify_results(run_id)

    step("测试结果汇总")
    for name, passed in results.items():
        status = "PASS ✓" if passed else "FAIL ✗"
        logger.info(f"  {name}: {status}")

    all_passed = all(results.values())
    logger.info(f"\n最终结果: {'全部通过 ✓' if all_passed else '存在失败 ✗'}")


if __name__ == "__main__":
    main()
