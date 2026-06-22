# -*- coding: utf-8 -*-
"""单表同步脚本 - 用法: python sync_single.py <table_name>"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.sync.engine import SyncEngine
from utils.logger import get_logger

logger = get_logger("sync_single")

def main():
    if len(sys.argv) < 2:
        print("Usage: python sync_single.py <table_name>")
        print("Tables: moneyflow, income, balancesheet, cashflow, fina_indicator, fina_audit")
        return

    table_name = sys.argv[1]
    engine = SyncEngine()

    logger.info(f"Starting sync for {table_name}...")
    try:
        engine.sync(table_name=table_name)
        logger.info(f"Sync completed for {table_name}")
    except Exception as e:
        logger.error(f"Sync failed for {table_name}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
