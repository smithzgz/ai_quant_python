# -*- coding: utf-8 -*-
import pandas as pd
from sqlalchemy import text
from data.database.connection import engine
from utils.logger import get_logger

logger = get_logger("quality_rules")


QUALITY_RULES = {
    "no_null_price": {
        "name": "价格非空检查",
        "check_cols": ["open", "high", "low", "close"],
        "threshold": 0.0,
        "level": "fail",
    },
    "positive_volume": {
        "name": "成交量非负检查",
        "check_cols": ["vol"],
        "condition": ">= 0",
        "level": "fail",
    },
    "reasonable_change": {
        "name": "涨跌幅合理性",
        "check_cols": ["pct_chg"],
        "threshold": 30.0,
        "level": "warn",
    },
    "price_consistency": {
        "name": "价格逻辑一致性",
        "rules": ["high >= low", "high >= open", "high >= close", "low <= open", "low <= close"],
        "level": "fail",
    },
    "no_missing_dates": {
        "name": "交易日完整性",
        "compare_with": "trade_cal",
        "level": "warn",
    },
    "adj_factor_positive": {
        "name": "复权因子正数",
        "check_cols": ["adj_factor"],
        "condition": "> 0",
        "level": "fail",
    },
    "reasonable_pe": {
        "name": "PE合理性",
        "check_cols": ["pe_ttm"],
        "threshold": 10000,
        "level": "warn",
    },
}


def check_no_null_price(df, rule_cfg):
    issues = []
    for col in rule_cfg["check_cols"]:
        if col in df.columns:
            null_count = df[col].isnull().sum()
            if null_count > 0:
                issues.append(f"column '{col}' has {null_count} null values")
    return issues


def check_positive_volume(df, rule_cfg):
    issues = []
    for col in rule_cfg["check_cols"]:
        if col in df.columns:
            neg_count = (df[col] < 0).sum()
            if neg_count > 0:
                issues.append(f"column '{col}' has {neg_count} negative values")
    return issues


def check_reasonable_change(df, rule_cfg):
    issues = []
    for col in rule_cfg["check_cols"]:
        if col in df.columns:
            extreme = (df[col].abs() > rule_cfg["threshold"]).sum()
            if extreme > 0:
                issues.append(f"column '{col}' has {extreme} values exceeding ±{rule_cfg['threshold']}%")
    return issues


def check_price_consistency(df, rule_cfg):
    issues = []
    required = ["high", "low", "open", "close"]
    if all(c in df.columns for c in required):
        bad = ((df["high"] < df["low"]) |
               (df["high"] < df["open"]) |
               (df["high"] < df["close"]) |
               (df["low"] > df["open"]) |
               (df["low"] > df["close"])).sum()
        if bad > 0:
            issues.append(f"{bad} rows violate price consistency (high>=low>=open/close)")
    return issues


def check_no_missing_dates(df, rule_cfg, table_name: str = None, check_date=None):
    issues = []
    if "trade_date" not in df.columns:
        return issues

    with engine.connect() as conn:
        if check_date:
            result = conn.execute(
                text("SELECT COUNT(*) FROM trade_cal WHERE is_open = 1 AND cal_date <= :d"),
                {"d": check_date},
            ).scalar()
        else:
            result = conn.execute(
                text("SELECT COUNT(*) FROM trade_cal WHERE is_open = 1")
            ).scalar()

        actual = df["trade_date"].nunique() if not df.empty else 0
        if result and actual < result * 0.9:
            issues.append(f"Only {actual}/{result} trade dates found (< 90%)")
    return issues


def check_adj_factor_positive(df, rule_cfg):
    issues = []
    if "adj_factor" in df.columns:
        bad = (df["adj_factor"] <= 0).sum()
        if bad > 0:
            issues.append(f"{bad} rows with adj_factor <= 0")
    return issues


def check_reasonable_pe(df, rule_cfg):
    issues = []
    for col in rule_cfg["check_cols"]:
        if col in df.columns:
            extreme = (df[col].abs() > rule_cfg["threshold"]).sum()
            if extreme > 0:
                issues.append(f"column '{col}' has {extreme} values exceeding ±{rule_cfg['threshold']}")
    return issues


RULE_CHECKERS = {
    "no_null_price": check_no_null_price,
    "positive_volume": check_positive_volume,
    "reasonable_change": check_reasonable_change,
    "price_consistency": check_price_consistency,
    "no_missing_dates": check_no_missing_dates,
    "adj_factor_positive": check_adj_factor_positive,
    "reasonable_pe": check_reasonable_pe,
}
