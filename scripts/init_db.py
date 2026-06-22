# -*- coding: utf-8 -*-
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data.database.timescale import init_all_tables
from utils.logger import get_logger

logger = get_logger("init_db")


def main():
    logger.info("Initializing database and tables...")
    init_all_tables()
    logger.info("Done. All tables created and TimescaleDB configured.")


if __name__ == "__main__":
    main()
