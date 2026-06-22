# -*- coding: utf-8 -*-
from datetime import date, datetime
import pandas as pd
from sqlalchemy import text
from data.database.connection import engine, SessionLocal
from data.database.models import DataQualityLog
from data.quality.rules import QUALITY_RULES, RULE_CHECKERS
from utils.logger import get_logger

logger = get_logger("checker")


class QualityChecker:
    def check_table(self, table_name: str, rule_names: list = None, check_date: date = None):
        cfg_rules = self._get_rules_for_table(table_name)
        if rule_names:
            cfg_rules = {k: v for k, v in cfg_rules.items() if k in rule_names}

        if not cfg_rules:
            logger.info(f"No quality rules for {table_name}")
            return []

        df = self._load_data(table_name, check_date)
        if df is None or df.empty:
            logger.warning(f"No data to check for {table_name}")
            return []

        results = []
        for rule_name, rule_cfg in cfg_rules.items():
            checker = RULE_CHECKERS.get(rule_name)
            if not checker:
                continue

            kwargs = {"df": df, "rule_cfg": rule_cfg}
            if rule_name == "no_missing_dates":
                kwargs["table_name"] = table_name
                kwargs["check_date"] = check_date

            issues = checker(**kwargs)

            status = "pass" if not issues else rule_cfg.get("level", "warn")
            result = {
                "table_name": table_name,
                "check_date": check_date or date.today(),
                "rule_name": rule_name,
                "status": status,
                "total_rows": len(df),
                "issue_count": len(issues),
                "details": issues,
            }
            results.append(result)
            self._save_result(result)

            log_fn = logger.info if status == "pass" else logger.warning
            log_fn(f"Quality [{status}] {table_name}/{rule_name}: {len(issues)} issues")

        return results

    def _get_rules_for_table(self, table_name: str) -> dict:
        from config.data_sync_config import DATA_SYNC_TASKS

        cfg = DATA_SYNC_TASKS.get(table_name, {})
        rule_names = cfg.get("quality_rules", [])
        return {k: v for k, v in QUALITY_RULES.items() if k in rule_names}

    def _load_data(self, table_name: str, check_date: date = None) -> pd.DataFrame:
        sql = f"SELECT * FROM {table_name}"
        params = {}
        if check_date:
            sql += " WHERE trade_date = :d"
            params["d"] = check_date
        sql += " LIMIT 100000"

        with engine.connect() as conn:
            return pd.read_sql(text(sql), conn, params=params)

    def _save_result(self, result: dict):
        session = SessionLocal()
        try:
            log = DataQualityLog(
                table_name=result["table_name"],
                check_date=result["check_date"],
                rule_name=result["rule_name"],
                status=result["status"],
                total_rows=result["total_rows"],
                issue_count=result["issue_count"],
                details=result["details"],
            )
            session.add(log)
            session.commit()
        finally:
            session.close()
