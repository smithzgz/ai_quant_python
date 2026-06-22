# -*- coding: utf-8 -*-
import logging
import os
from config.settings import settings

MODULE_MAP = {
    "sync_engine": "sync",
    "tushare_client": "sync",
    "scheduler": "sync",
    "sync_single": "sync",
    "sync_fin": "sync",
    "sync_fin_rest": "sync",
    "full_sync": "sync",
    "run_sync": "sync",
    "vbt_engine": "backtest",
    "data_loader": "backtest",
    "result_extractor": "backtest",
    "persistor": "backtest",
    "queries": "backtest",
    "registry": "backtest",
    "backtest_all": "backtest",
    "base_repo": "data",
    "timescale": "data",
    "init_db": "data",
    "create_fin_tables": "data",
    "checker": "quality",
    "reporter": "quality",
    "quality_rules": "quality",
    "trade_calendar": "utils",
    "e2e_test": "test",
}

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str = "ai_quant") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    log_dir = settings.LOG_DIR
    os.makedirs(log_dir, exist_ok=True)

    fh_all = logging.FileHandler(os.path.join(log_dir, "app.log"), encoding="utf-8")
    fh_all.setLevel(logging.DEBUG)
    fh_all.setFormatter(fmt)
    logger.addHandler(fh_all)

    module = MODULE_MAP.get(name)
    if module:
        fh_mod = logging.FileHandler(
            os.path.join(log_dir, f"{module}.log"), encoding="utf-8"
        )
        fh_mod.setLevel(logging.DEBUG)
        fh_mod.setFormatter(fmt)
        logger.addHandler(fh_mod)

    return logger
