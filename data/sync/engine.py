# -*- coding: utf-8 -*-
import threading
import pandas as pd
from datetime import datetime, date, timedelta
from sqlalchemy import func
from data.database.connection import engine, SessionLocal
from data.database.base_repo import BaseRepo
from data.database.models import SyncLog, SyncCheckpoint
from data.sync.tushare_client import TushareClient
from config.data_sync_config import DATA_SYNC_TASKS
from utils.logger import get_logger

logger = get_logger("sync_engine")

# Global registry: table_name -> {"running": bool, "cancel": bool}
RUNNING_SYNCS = {}
_SYNC_LOCK = threading.Lock()


def _convert_dates(df, cfg):
    if df is None or df.empty:
        return df
    fields = cfg.get("fields", {})
    for col in df.columns:
        if col in fields and fields[col][0] == "date":
            if df[col].dtype == object:
                df[col] = pd.to_datetime(df[col], format="%Y%m%d", errors="coerce").dt.date
    return df


class SyncEngine:
    def __init__(self):
        self.client = TushareClient()
        self.repo = BaseRepo(None)

    @staticmethod
    def _acquire(table_name: str) -> bool:
        with _SYNC_LOCK:
            state = RUNNING_SYNCS.get(table_name)
            if state and state.get("running"):
                return False
            RUNNING_SYNCS[table_name] = {"running": True, "cancel": False}
            return True

    @staticmethod
    def _release(table_name: str) -> None:
        with _SYNC_LOCK:
            RUNNING_SYNCS.pop(table_name, None)

    def sync(self, table_name: str = None, mode: str = None, max_pages: int = None, cancel_check=None, stale_days: int = None):
        if table_name:
            cfg = DATA_SYNC_TASKS.get(table_name)
            if not cfg:
                logger.error(f"Unknown table: {table_name}")
                return
            if not cfg.get("enabled", True):
                logger.info(f"Table {table_name} is disabled, skipping")
                return
            if not self._acquire(table_name):
                logger.warning(f"{table_name}: sync already running, skip")
                return
            def _cancel():
                if cancel_check and cancel_check():
                    return True
                return RUNNING_SYNCS.get(table_name, {}).get("cancel", False)
            try:
                self._sync_table(table_name, cfg, mode, max_pages, _cancel)
            finally:
                self._release(table_name)
        else:
            stale_cutoff = None
            if stale_days is not None:
                stale_cutoff = date.today() - timedelta(days=stale_days)
            sorted_tasks = sorted(
                DATA_SYNC_TASKS.items(),
                key=lambda x: x[1].get("priority", 99),
            )
            for name, cfg in sorted_tasks:
                if not cfg.get("enabled", True):
                    continue
                if stale_cutoff is not None and not self._is_stale(name, stale_cutoff):
                    logger.info(f"Catch-up skip {name}: checkpoint is fresh")
                    continue
                if not self._acquire(name):
                    logger.warning(f"{name}: sync already running, skip")
                    continue
                try:
                    def _cancel(n=name):
                        if cancel_check and cancel_check():
                            return True
                        return RUNNING_SYNCS.get(n, {}).get("cancel", False)
                    self._sync_table(name, cfg, mode, max_pages, _cancel)
                except Exception as e:
                    logger.error(f"Sync failed for {name}: {e}")
                finally:
                    self._release(name)

    def _is_stale(self, table_name: str, cutoff: date) -> bool:
        try:
            cp = self._get_checkpoint(table_name)
        except Exception as e:
            logger.warning(f"{table_name}: checkpoint lookup failed ({e}), treat as stale")
            cp = None
        if cp is None:
            session = SessionLocal()
            try:
                last_ok = (
                    session.query(func.max(SyncLog.start_time))
                    .filter(SyncLog.table_name == table_name, SyncLog.status == "completed")
                    .scalar()
                )
            finally:
                session.close()
            if last_ok:
                cp = last_ok.date() if isinstance(last_ok, datetime) else last_ok
        return cp is None or cp < cutoff

    def _sync_table(self, table_name: str, cfg: dict, mode_override: str = None, max_pages_override: int = None, cancel_check=None):
        actual_mode = mode_override or cfg.get("mode", "once")
        logger.info(f"===== Sync start: {table_name} (mode={actual_mode}) =====")

        session = SessionLocal()
        sync_log = SyncLog(
            table_name=table_name,
            sync_mode=actual_mode,
            start_time=datetime.now(),
            status="running",
        )
        session.add(sync_log)
        session.commit()
        sync_log_id = sync_log.id

        synced_start = None
        synced_end = None

        try:
            # Check for custom sync function
            sync_func_path = cfg.get("sync_func")
            if sync_func_path:
                total_rows, synced_start, synced_end = self._sync_custom(table_name, cfg, sync_func_path, cancel_check, max_pages_override, actual_mode)
            else:
                api_date_type = cfg.get("api_date_type", "single")

                if api_date_type == "code":
                    total_rows, synced_start, synced_end = self._sync_by_code(table_name, cfg, cancel_check)
                elif actual_mode == "once":
                    total_rows, synced_start, synced_end = self._sync_once(table_name, cfg)
                elif actual_mode == "incremental":
                    total_rows, synced_start, synced_end = self._sync_incremental(table_name, cfg, cancel_check)
                elif actual_mode == "full":
                    total_rows, synced_start, synced_end = self._sync_full(table_name, cfg, cancel_check)
                else:
                    raise ValueError(f"Unknown mode: {actual_mode}")

            verify_ok = True
            verify_msg = ""
            sample_size = cfg.get("verify_sample_size", 5)
            if sample_size and synced_start and synced_end and total_rows > 0:
                try:
                    from data.quality.sync_verifier import SyncVerifier
                    verifier = SyncVerifier()
                    v_result = verifier.verify(table_name, synced_start, synced_end, sample_size)
                    if v_result and v_result["status"] == "fail":
                        verify_ok = False
                        verify_msg = f"Verify failed: {v_result['failed']}/{v_result['total_checks']} checks failed"
                        verify_msg += f" | mismatches: {'; '.join(v_result.get('mismatches', [])[:5])}"
                        logger.warning(f"===== {verify_msg} =====")
                except Exception as ve:
                    logger.error(f"Verify error for {table_name}: {ve}")

            sync_log = session.query(SyncLog).get(sync_log_id)
            if verify_ok:
                sync_log.status = "completed"
            else:
                sync_log.status = "failed"
                sync_log.error_message = verify_msg
            sync_log.end_time = datetime.now()
            sync_log.rows_inserted = total_rows
            session.commit()

            if verify_ok:
                logger.info(f"===== Sync completed: {table_name} ({total_rows} rows) =====")
            else:
                logger.error(f"===== Sync completed but VERIFY FAILED: {table_name} =====")
                raise Exception(verify_msg)

        except Exception as e:
            sync_log = session.query(SyncLog).get(sync_log_id)
            if "cancelled" in str(e).lower():
                sync_log.status = "cancelled"
            else:
                sync_log.status = "failed"
            sync_log.end_time = datetime.now()
            sync_log.error_message = str(e)
            session.commit()
            logger.error(f"===== Sync {sync_log.status}: {table_name} {e} =====")
            raise
        finally:
            session.close()

    def _sync_once(self, table_name: str, cfg: dict):
        api_name = cfg["api"]
        params = cfg.get("params", {})
        fields_str = ",".join(cfg["fields"].keys())

        data = self.client.call(api_name, fields=fields_str, **params)
        if data is None or data.empty:
            logger.info(f"{table_name}: no data returned")
            return 0, None, None

        data = _convert_dates(data, cfg)
        self._write_df(table_name, data, cfg, truncate=True)
        self._update_checkpoint(table_name, date.today())
        return len(data), date.today(), date.today()

    def _sync_incremental(self, table_name: str, cfg: dict, cancel_check=None):
        latest = self._get_checkpoint(table_name)
        oldest = cfg.get("oldest_date")

        if oldest and isinstance(oldest, str):
            oldest = datetime.strptime(oldest, "%Y%m%d").date()

        if latest is None and oldest:
            latest = oldest

        if latest is None:
            logger.warning(f"{table_name}: no checkpoint and no oldest_date, skipping")
            return 0, None, None

        today = date.today()
        if latest > today:
            logger.info(f"{table_name}: checkpoint {latest} is in the future, capping to {today}")
            latest = today

        trade_dates = self._get_trade_dates(latest)
        if not trade_dates:
            logger.info(f"{table_name}: no new trade dates to sync")
            return 0, None, None

        total_rows = 0
        first_failure = None
        date_field = cfg.get("date_field", "trade_date")
        api_name = cfg["api"]
        fields_str = ",".join(cfg["fields"].keys())
        extra_params = cfg.get("params", {})
        date_type = cfg.get("api_date_type", "single")

        first_date = trade_dates[0]
        last_date = trade_dates[-1]

        for td in trade_dates:
            if cancel_check and cancel_check():
                logger.info(f"{table_name}: sync cancelled at {td}")
                raise Exception(f"sync cancelled at {td}")
            td_str = td.strftime("%Y%m%d")
            try:
                if date_type == "single":
                    data = self.client.call(
                        api_name, **{date_field: td_str}, fields=fields_str, **extra_params
                    )
                elif date_type == "range":
                    data = self.client.call(
                        api_name, start_date=td_str, end_date=td_str,
                        fields=fields_str, **extra_params
                    )
                elif date_type == "ann":
                    data = self.client.call(
                        api_name, ann_date=td_str, fields=fields_str, **extra_params
                    )
                else:
                    data = self.client.call(
                        api_name, **{date_type: td_str}, fields=fields_str, **extra_params
                    )

                if data is not None and not data.empty:
                    data = _convert_dates(data, cfg)
                    self._write_df(table_name, data, cfg)
                    total_rows += len(data)
                    self._update_checkpoint(table_name, td)

                logger.info(f"{table_name} {td_str} done ({total_rows} rows so far)")

            except Exception as e:
                if "cancel" in str(e).lower():
                    raise
                logger.error(f"{table_name} {td_str} failed: {e}")
                if total_rows == 0:
                    raise
                if first_failure is None:
                    first_failure = td

        if first_failure is not None:
            rollback = first_failure - timedelta(days=1)
            self._update_checkpoint(table_name, rollback)
            raise Exception(
                f"sync incomplete: {table_name} failed at {first_failure}, "
                f"checkpoint rolled back to {rollback} for retry next run"
            )

        return total_rows, first_date, last_date

    def _sync_full(self, table_name: str, cfg: dict, cancel_check=None):
        rows, start, end = self._sync_incremental(table_name, {**cfg, "oldest_date": cfg.get("oldest_date", "19910101")}, cancel_check)
        return rows, start, end

    def _sync_by_code(self, table_name: str, cfg: dict, cancel_check=None):
        code_source = cfg.get("code_source", "stock_basic")
        code_field = cfg.get("code_field", "ts_code")
        code_source_column = cfg.get("code_source_column", "ts_code")
        code_filter = cfg.get("code_filter", "list_status = 'L'")
        with engine.connect() as conn:
            from sqlalchemy import text
            result = conn.execute(text(
                f"SELECT {code_source_column} FROM {code_source} WHERE {code_filter} ORDER BY {code_source_column}"
            ))
            stocks = [row[0] for row in result.fetchall()]

        if not stocks:
            logger.warning(f"{table_name}: no stocks found")
            return 0, None, None

        api_name = cfg["api"]
        fields_str = ",".join(cfg["fields"].keys())
        total_rows = 0
        failed_codes = 0

        overlap_days = int(cfg.get("sync_overlap_days", 3))
        cp = self._get_checkpoint(table_name)
        start_date = None
        if cp:
            start_date = (cp - timedelta(days=overlap_days)).strftime("%Y%m%d")
        if start_date:
            logger.info(f"{table_name}: by-code incremental from start_date={start_date} (checkpoint={cp})")

        for i, ts_code in enumerate(stocks):
            if cancel_check and cancel_check():
                logger.info(f"{table_name}: sync cancelled at {ts_code}")
                raise Exception(f"sync cancelled at {ts_code}")
            try:
                kwargs = {code_field: ts_code}
                if start_date:
                    kwargs["start_date"] = start_date
                data = self.client.call(api_name, **kwargs, fields=fields_str)
                if data is not None and not data.empty:
                    data = _convert_dates(data, cfg)
                    self._write_df(table_name, data, cfg)
                    total_rows += len(data)

                if (i + 1) % 100 == 0:
                    logger.info(f"{table_name}: {i+1}/{len(stocks)} done ({total_rows} rows so far)")

            except Exception as e:
                failed_codes += 1
                logger.error(f"{table_name} {ts_code} failed: {e}")

        if failed_codes:
            logger.warning(
                f"{table_name}: {failed_codes}/{len(stocks)} codes failed, "
                f"checkpoint kept at {cp} for retry next run"
            )
        else:
            self._update_checkpoint(table_name, date.today())
        logger.info(f"{table_name}: completed {len(stocks)} codes, {total_rows} total rows")
        return total_rows, date.today(), date.today()

    def _get_trade_dates(self, since: date) -> list:
        today = date.today()
        with engine.connect() as conn:
            from sqlalchemy import text
            result = conn.execute(
                text("SELECT cal_date FROM trade_cal WHERE is_open = 1 AND cal_date > :since AND cal_date <= :today ORDER BY cal_date"),
                {"since": since, "today": today},
            )
            return [row[0] for row in result.fetchall()]

    def _write_df(self, table_name: str, df: pd.DataFrame, cfg: dict, truncate: bool = False):
        if truncate:
            with engine.connect() as conn:
                from sqlalchemy import text
                conn.execute(text(f"TRUNCATE TABLE {table_name}"))
                conn.commit()

        pk_cols = [k for k, v in cfg.get("fields", {}).items() if len(v) >= 3 and v[2] is True]

        # COPY 化批量写入（MINUTE_DATA_PLAN.md 5.3，日线/分钟共用）
        from data.sync.bulk_writer import copy_upsert_df
        copy_upsert_df(table_name, df, pk_cols=pk_cols)

    def _get_checkpoint(self, table_name: str) -> date:
        session = SessionLocal()
        try:
            cp = session.query(SyncCheckpoint).filter_by(table_name=table_name).first()
            if cp:
                return cp.last_sync_date
            latest = self.repo.get_latest_date(table_name)
            if latest:
                return latest if isinstance(latest, date) else latest.date()
            return None
        finally:
            session.close()

    def _sync_custom(self, table_name: str, cfg: dict, sync_func_path: str, cancel_check=None, max_pages_override: int = None, mode_override: str = None):
        """Execute a custom sync function (non-tushare sources)."""
        import importlib
        module_path, func_name = sync_func_path.rsplit('.', 1)
        module = importlib.import_module(module_path)
        sync_func = getattr(module, func_name)

        max_pages = max_pages_override if max_pages_override is not None else cfg.get("max_pages", 0)
        batch_size = cfg.get("batch_size", 50)
        actual_mode = mode_override or cfg.get("mode", "full")

        # Get raw psycopg2 connection from SQLAlchemy engine
        raw_conn = engine.raw_connection()

        try:
            result = sync_func(
                raw_conn,
                mode=actual_mode,
                max_pages=max_pages,
                batch_size=batch_size,
            )
        finally:
            raw_conn.close()

        total_rows = result.get("total_records", 0) if isinstance(result, dict) else 0
        return total_rows, None, None

    def _update_checkpoint(self, table_name: str, sync_date: date):
        session = SessionLocal()
        try:
            cp = session.query(SyncCheckpoint).filter_by(table_name=table_name).first()
            if cp is None:
                cp = SyncCheckpoint(table_name=table_name, last_sync_date=sync_date)
                session.add(cp)
            else:
                cp.last_sync_date = sync_date
            session.commit()
        finally:
            session.close()
