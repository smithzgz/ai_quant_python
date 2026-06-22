# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data.database.timescale import init_all_tables
from data.sync.engine import SyncEngine
from config.data_sync_config import DATA_SYNC_TASKS
from utils.logger import get_logger

logger = get_logger("run_sync")


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_sync.py <table_name | --all | --init> [--full]")
        print(f"Available tables: {', '.join(sorted(DATA_SYNC_TASKS.keys()))}")
        return

    cmd = sys.argv[1]
    mode = "full" if "--full" in sys.argv else None

    if cmd == "--init":
        logger.info("Initializing database...")
        init_all_tables()
        logger.info("Database initialized.")
        return

    engine = SyncEngine()

    if cmd == "--all":
        engine.sync(mode=mode)
    elif cmd in DATA_SYNC_TASKS:
        engine.sync(table_name=cmd, mode=mode)
    else:
        print(f"Unknown table: {cmd}")
        print(f"Available: {', '.join(sorted(DATA_SYNC_TASKS.keys()))}")


if __name__ == "__main__":
    main()
