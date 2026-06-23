# -*- coding: utf-8 -*-
import math
import random
import json
from datetime import datetime, date, timedelta
from data.database.connection import engine, SessionLocal
from data.database.models import SyncLog, DataQualityLog
from data.sync.tushare_client import TushareClient
from config.data_sync_config import DATA_SYNC_TASKS
from utils.logger import get_logger

logger = get_logger("sync_verifier")

VERIFY_FIELDS = {
    "daily": ["open", "high", "low", "close", "vol", "amount"],
    "daily_basic": ["close", "turnover_rate", "pe_ttm", "pb", "total_mv"],
    "adj_factor": ["adj_factor"],
    "fund_daily": ["open", "high", "low", "close", "vol", "amount"],
    "moneyflow": ["buy_sm_vol", "sell_sm_vol", "net_mf_vol"],
    "income": ["total_revenue", "n_income", "basic_eps"],
    "balancesheet": ["total_assets", "total_liab"],
    "cashflow": ["n_cashflow_act"],
    "fina_indicator": ["eps", "roe", "roa"],
    "fina_audit": ["audit_result"],
}

DEFAULT_VERIFY_FIELDS = ["open", "close", "vol"]


class SyncVerifier:
    def __init__(self):
        self.client = TushareClient()

    def verify(self, table_name: str, synced_start: date, synced_end: date, sample_size: int = 5):
        cfg = DATA_SYNC_TASKS.get(table_name)
        if not cfg:
            return None

        dates = self._sample_dates(synced_start, synced_end, sample_size)
        if not dates:
            logger.info(f"[Verify] {table_name}: no dates to sample")
            return None

        api_name = cfg.get("api", table_name)
        date_field = cfg.get("date_field", "trade_date")
        api_date_type = cfg.get("api_date_type", "single")
        date_type = cfg.get("api_date_type", "single")
        fields_str = ",".join(cfg.get("fields", {}).keys())
        extra_params = cfg.get("params", {})

        verify_cols = VERIFY_FIELDS.get(table_name, DEFAULT_VERIFY_FIELDS)

        results = []
        for d in dates:
            try:
                api_result = self._fetch_from_api(
                    api_name, d, date_field, date_type, fields_str, extra_params
                )
                db_result = self._fetch_from_db(table_name, d, date_field)

                comparison = self._compare(d, api_result, db_result, verify_cols)
                results.append(comparison)
            except Exception as e:
                logger.error(f"[Verify] {table_name} {d} error: {e}")
                results.append({
                    "date": d.isoformat(),
                    "status": "error",
                    "error": str(e),
                })

        summary = self._build_summary(table_name, results)
        self._save_to_db(table_name, summary, results)

        failed = [r for r in results if r["status"] != "pass"]
        if failed:
            logger.warning(
                f"[Verify] {table_name}: {len(failed)}/{len(results)} checks FAILED"
            )
        else:
            logger.info(f"[Verify] {table_name}: all {len(results)} checks passed")

        return summary

    def _sample_dates(self, start: date, end: date, count: int) -> list:
        with engine.connect() as conn:
            from sqlalchemy import text
            result = conn.execute(
                text(
                    "SELECT cal_date FROM trade_cal "
                    "WHERE is_open = 1 AND cal_date >= :start AND cal_date <= :end "
                    "ORDER BY cal_date"
                ),
                {"start": start, "end": end},
            )
            all_dates = [row[0] for row in result.fetchall()]

        if not all_dates:
            return []

        if len(all_dates) <= count:
            return all_dates

        return sorted(random.sample(all_dates, count))

    def _fetch_from_api(self, api_name, d, date_field, date_type, fields_str, extra_params):
        d_str = d.strftime("%Y%m%d")
        if date_type == "code":
            return None
        elif date_type == "single":
            return self.client.call(api_name, **{date_field: d_str}, fields=fields_str, **extra_params)
        elif date_type == "range":
            return self.client.call(api_name, start_date=d_str, end_date=d_str, fields=fields_str, **extra_params)
        else:
            return self.client.call(api_name, **{date_type: d_str}, fields=fields_str, **extra_params)

    def _fetch_from_db(self, table_name, d, date_field):
        with engine.connect() as conn:
            from sqlalchemy import text
            result = conn.execute(
                text(f"SELECT * FROM {table_name} WHERE {date_field} = :d"),
                {"d": d},
            )
            cols = result.keys()
            rows = result.fetchall()
        if not rows:
            return []
        return [dict(zip(cols, row)) for row in rows]

    def _compare(self, d, api_data, db_rows, verify_cols):
        api_empty = api_data is None or (hasattr(api_data, "empty") and api_data.empty)
        db_empty = len(db_rows) == 0

        if api_empty and db_empty:
            return {"date": d.isoformat(), "status": "pass", "message": "both empty"}
        if api_empty:
            return {"date": d.isoformat(), "status": "fail", "message": "API returned empty, DB has data"}
        if db_empty:
            return {"date": d.isoformat(), "status": "fail", "message": "DB is empty, API has data"}

        api_df = api_data if hasattr(api_data, "iterrows") else None
        if api_df is None:
            return {"date": d.isoformat(), "status": "pass", "message": "skip non-dataframe"}

        api_count = len(api_df)
        db_count = len(db_rows)
        mismatches = []

        if api_count != db_count:
            mismatches.append(f"row count: API={api_count}, DB={db_count}")

        db_map = {}
        for row in db_rows:
            pk = row.get("ts_code") or row.get("code") or row.get("name", "")
            db_map[str(pk)] = row

        for _, api_row in api_df.iterrows():
            pk_val = str(api_row.get("ts_code") or api_row.get("code") or api_row.get("name", ""))
            db_row = db_map.get(pk_val)
            if not db_row:
                mismatches.append(f"{pk_val}: not found in DB")
                continue

            for col in verify_cols:
                if col not in api_row.index or col not in db_row:
                    continue
                api_val = api_row[col]
                db_val = db_row[col]
                api_is_none = api_val is None or (isinstance(api_val, float) and math.isnan(api_val))
                db_is_none = db_val is None or (isinstance(db_val, float) and math.isnan(db_val))
                if api_is_none and db_is_none:
                    continue
                if api_is_none or db_is_none:
                    mismatches.append(f"{pk_val}.{col}: API={api_val}, DB={db_val}")
                    continue
                try:
                    api_f = float(api_val)
                    db_f = float(db_val)
                    if abs(api_f - db_f) > 0.01 and abs(api_f - db_f) / max(abs(api_f), 1e-10) > 0.001:
                        mismatches.append(f"{pk_val}.{col}: API={api_val}, DB={db_val}")
                except (ValueError, TypeError):
                    if str(api_val) != str(db_val):
                        mismatches.append(f"{pk_val}.{col}: API={api_val}, DB={db_val}")

        if mismatches:
            return {
                "date": d.isoformat(),
                "status": "fail",
                "api_count": api_count,
                "db_count": db_count,
                "mismatches": mismatches[:20],
            }
        return {
            "date": d.isoformat(),
            "status": "pass",
            "api_count": api_count,
            "db_count": db_count,
        }

    def _build_summary(self, table_name, results):
        passed = sum(1 for r in results if r["status"] == "pass")
        failed = sum(1 for r in results if r["status"] == "fail")
        errors = sum(1 for r in results if r["status"] == "error")
        all_mismatches = []
        for r in results:
            if r.get("mismatches"):
                all_mismatches.extend(r["mismatches"])

        return {
            "table_name": table_name,
            "total_checks": len(results),
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "status": "pass" if failed == 0 and errors == 0 else "fail",
            "mismatches": all_mismatches[:50],
            "details": results,
        }

    def _save_to_db(self, table_name, summary, results):
        session = SessionLocal()
        try:
            log = DataQualityLog(
                table_name=table_name,
                check_date=date.today(),
                rule_name="sync_consistency",
                status=summary["status"],
                total_rows=summary["total_checks"],
                issue_count=summary["failed"] + summary["errors"],
                details=summary,
            )
            session.add(log)
            session.commit()
        finally:
            session.close()
