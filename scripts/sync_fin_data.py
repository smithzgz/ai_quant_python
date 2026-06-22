# -*- coding: utf-8 -*-
"""同步财务数据表（moneyflow + 4个财务报表）"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.sync.engine import SyncEngine
from utils.logger import get_logger

logger = get_logger("sync_fin")


def main():
    engine = SyncEngine()

    tables = ["moneyflow", "income", "balancesheet", "cashflow", "fina_indicator", "fina_audit"]

    for table in tables:
        logger.info(f"Starting sync for {table}...")
        try:
            engine.sync(table_name=table)
            logger.info(f"Sync completed for {table}")
        except Exception as e:
            logger.error(f"Sync failed for {table}: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
