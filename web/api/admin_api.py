# -*- coding: utf-8 -*-
import os
import chardet
from datetime import datetime, date
from fastapi import APIRouter, Query, BackgroundTasks
from data.database.connection import engine, SessionLocal
from data.database.models import SyncLog, SyncCheckpoint, DataQualityLog
from config.data_sync_config import DATA_SYNC_TASKS
from config.settings import settings

router = APIRouter()

TASK_STATUS = {}

from data.sync.engine import RUNNING_SYNCS as _running_syncs

SERVER_START_TIME = datetime.now()


def cleanup_stale_syncs():
    session = SessionLocal()
    try:
        stale = session.query(SyncLog).filter(
            SyncLog.status == "running",
            SyncLog.start_time < SERVER_START_TIME,
        ).all()
        for log in stale:
            log.status = "cancelled"
            log.end_time = datetime.now()
            log.error_message = "Auto-cancelled: server restarted while task was running"
            TASK_STATUS.pop(log.table_name, None)
            _running_syncs.pop(log.table_name, None)
        if stale:
            session.commit()
            return len(stale)
        return 0
    finally:
        session.close()


def _init():
    cleanup_stale_syncs()


_init()


def _run_sync_task(table_name, mode=None, max_pages=None):
    TASK_STATUS[table_name] = "running"
    try:
        from data.sync.engine import SyncEngine
        eng = SyncEngine()
        eng.sync(table_name=table_name, mode=mode, max_pages=max_pages)
        TASK_STATUS[table_name] = "success"
    except Exception as e:
        if "cancelled" in str(e).lower():
            TASK_STATUS[table_name] = "cancelled"
        else:
            TASK_STATUS[table_name] = f"error: {e}"


def _read_log_file(filepath, lines=200, keyword=None):
    if not os.path.exists(filepath):
        return 0, ""
    raw = open(filepath, "rb").read()
    if not raw:
        return 0, ""
    det = chardet.detect(raw)
    enc = det.get("encoding", "utf-8") or "utf-8"
    if enc.lower() in ("ascii",):
        enc = "utf-8"
    text = raw.decode(enc, errors="replace")
    all_lines = text.splitlines(keepends=True)
    if keyword:
        kw = keyword.lower()
        all_lines = [l for l in all_lines if kw in l.lower()]
    total = len(all_lines)
    tail = all_lines[-lines:]
    return total, "".join(tail)


@router.get("/tasks")
def list_tasks():
    cleanup_stale_syncs()
    session = SessionLocal()
    try:
        result = []
        for name, cfg in DATA_SYNC_TASKS.items():
            last_log = (
                session.query(SyncLog)
                .filter(SyncLog.table_name == name)
                .order_by(SyncLog.start_time.desc())
                .first()
            )
            last_status = last_log.status if last_log else "idle"
            last_time = last_log.start_time.isoformat() if last_log and last_log.start_time else None
            last_rows = last_log.rows_inserted if last_log else None
            result.append({
                "table_name": name,
                "name": cfg.get("name", name),
                "api": cfg.get("api", name),
                "mode": cfg.get("mode", "unknown"),
                "schedule": cfg.get("schedule", ""),
                "priority": cfg.get("priority", 99),
                "date_field": cfg.get("date_field", ""),
                "api_date_type": cfg.get("api_date_type", ""),
                "is_timescale": cfg.get("is_timescale", False),
                "field_count": len(cfg.get("fields", {})),
                "status": TASK_STATUS.get(name, last_status),
                "last_time": last_time,
                "last_rows": last_rows,
            })
        result.sort(key=lambda x: x["priority"])
        return result
    finally:
        session.close()


CLASSIFICATION_MAP = {
    "trade_cal": "基础信息",
    "stock_basic": "基础信息",
    "fund_basic": "基础信息",
    "index_basic": "基础信息",
    "daily": "行情数据",
    "daily_basic": "行情数据",
    "adj_factor": "行情数据",
    "fund_daily": "行情数据",
    "index_daily": "行情数据",
    "index_weekly": "行情数据",
    "index_monthly": "行情数据",
    "index_dailybasic": "行情数据",
    "moneyflow": "资金流向",
    "income": "财务报表",
    "balancesheet": "财务报表",
    "cashflow": "财务报表",
    "fina_indicator": "财务指标",
    "fina_audit": "财务指标",
    "index_weight": "指数成分",
    "sohu_jlp": "Analyst Recommendations",
    "eastmoney_report": "Analyst Recommendations",
    "bond_basic": "可转债",
    "cb_issue": "可转债",
    "cb_share": "可转债",
    "cb_daily": "可转债",
    "bond_cb_index": "可转债",
}

CLASSIFICATION_ORDER = ["基础信息", "行情数据", "资金流向", "财务报表", "财务指标", "指数成分", "Analyst Recommendations", "可转债"]


@router.get("/dashboard")
def dashboard():
    session = SessionLocal()
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE schemaname = 'public'"
            ))
            row_counts = {row[0]: row[1] for row in result.fetchall()}

        tables = []
        for name, cfg in DATA_SYNC_TASKS.items():
            last_log = (
                session.query(SyncLog)
                .filter(SyncLog.table_name == name)
                .order_by(SyncLog.start_time.desc())
                .first()
            )
            cp = (
                session.query(SyncCheckpoint)
                .filter(SyncCheckpoint.table_name == name)
                .first()
            )
            tables.append({
                "table_name": name,
                "name": cfg.get("name", name),
                "api": cfg.get("api", name),
                "mode": cfg.get("mode", "unknown"),
                "schedule": cfg.get("schedule", ""),
                "priority": cfg.get("priority", 99),
                "date_field": cfg.get("date_field", ""),
                "api_date_type": cfg.get("api_date_type", ""),
                "is_timescale": cfg.get("is_timescale", False),
                "fields": {k: {"type": v[0], "desc": v[1], "pk": v[2]} for k, v in cfg.get("fields", {}).items()},
                "classification": CLASSIFICATION_MAP.get(name, "其他"),
                "status": TASK_STATUS.get(name, last_log.status if last_log else "idle"),
                "last_sync_time": last_log.start_time.isoformat() if last_log and last_log.start_time else None,
                "row_count": row_counts.get(name, 0),
                "checkpoint_date": cp.last_sync_date.isoformat() if cp and cp.last_sync_date else None,
            })
        tables.sort(key=lambda x: (CLASSIFICATION_ORDER.index(x["classification"]) if x["classification"] in CLASSIFICATION_ORDER else 99, x["priority"]))
        return {
            "tables": tables,
            "classifications": CLASSIFICATION_ORDER,
        }
    finally:
        session.close()


@router.get("/task/{table_name}")
def task_detail(table_name: str, limit: int = Query(default=20, le=100)):
    if table_name not in DATA_SYNC_TASKS:
        return {"error": f"Unknown table: {table_name}"}
    cfg = DATA_SYNC_TASKS[table_name]
    session = SessionLocal()
    try:
        logs = (
            session.query(SyncLog)
            .filter(SyncLog.table_name == table_name)
            .order_by(SyncLog.start_time.desc())
            .limit(limit)
            .all()
        )
        cp = (
            session.query(SyncCheckpoint)
            .filter(SyncCheckpoint.table_name == table_name)
            .first()
        )
        total_runs = (
            session.query(SyncLog)
            .filter(SyncLog.table_name == table_name)
            .count()
        )
        success_runs = (
            session.query(SyncLog)
            .filter(SyncLog.table_name == table_name, SyncLog.status.in_(["success", "completed"]))
            .count()
        )
        failed_runs = (
            session.query(SyncLog)
            .filter(SyncLog.table_name == table_name, SyncLog.status == "failed")
            .count()
        )
        verify_logs = (
            session.query(DataQualityLog)
            .filter(
                DataQualityLog.table_name == table_name,
                DataQualityLog.rule_name == "sync_consistency",
            )
            .order_by(DataQualityLog.checked_at.desc())
            .limit(10)
            .all()
        )
        return {
            "table_name": table_name,
            "config": {
                "name": cfg.get("name", table_name),
                "api": cfg.get("api", table_name),
                "mode": cfg.get("mode", "unknown"),
                "schedule": cfg.get("schedule", ""),
                "priority": cfg.get("priority", 99),
                "date_field": cfg.get("date_field", ""),
                "api_date_type": cfg.get("api_date_type", ""),
                "is_timescale": cfg.get("is_timescale", False),
                "verify_sample_size": cfg.get("verify_sample_size", 0),
                "fields": {k: {"type": v[0], "desc": v[1], "pk": v[2]} for k, v in cfg.get("fields", {}).items()},
            },
            "checkpoint": {
                "last_sync_date": cp.last_sync_date.isoformat() if cp and cp.last_sync_date else None,
                "updated_at": cp.updated_at.isoformat() if cp and cp.updated_at else None,
            } if cp else None,
            "stats": {
                "total_runs": total_runs,
                "success": success_runs,
                "failed": failed_runs,
            },
            "verifications": [
                {
                    "id": v.id,
                    "check_date": v.check_date.isoformat() if v.check_date else None,
                    "status": v.status,
                    "total_checks": v.total_rows,
                    "issue_count": v.issue_count,
                    "details": v.details,
                    "checked_at": v.checked_at.isoformat() if v.checked_at else None,
                }
                for v in verify_logs
            ],
            "history": [
                {
                    "id": r.id,
                    "sync_mode": r.sync_mode,
                    "status": r.status,
                    "rows_fetched": r.rows_fetched,
                    "rows_inserted": r.rows_inserted,
                    "start_time": r.start_time.isoformat() if r.start_time else None,
                    "end_time": r.end_time.isoformat() if r.end_time else None,
                    "duration": (
                        (r.end_time - r.start_time).total_seconds()
                        if r.end_time and r.start_time
                        else None
                    ),
                    "error_message": r.error_message,
                }
                for r in logs
            ],
        }
    finally:
        session.close()


@router.get("/task/{table_name}/logs")
def task_logs(
    table_name: str,
    lines: int = Query(default=200, le=1000),
    keyword: str = Query(default=None),
    start_time: str = Query(default=None),
    end_time: str = Query(default=None),
):
    if table_name not in DATA_SYNC_TASKS:
        return {"error": f"Unknown table: {table_name}"}
    log_dir = settings.LOG_DIR
    candidates = [
        os.path.join(log_dir, "sync.log"),
        os.path.join(log_dir, "app.log"),
    ]
    log_file = None
    for p in candidates:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            log_file = p
            break
    if not log_file:
        return {"table_name": table_name, "lines": 0, "content": ""}
    raw = open(log_file, "rb").read()
    if not raw:
        return {"table_name": table_name, "lines": 0, "content": ""}
    det = chardet.detect(raw)
    enc = det.get("encoding", "utf-8") or "utf-8"
    if enc.lower() in ("ascii",):
        enc = "utf-8"
    text = raw.decode(enc, errors="replace")
    all_lines = text.splitlines(keepends=True)
    table_lower = table_name.lower()
    matched = [l for l in all_lines if table_lower in l.lower()]
    if start_time:
        st = start_time[:19].replace("T", " ")
        matched = [l for l in matched if l[:19] >= st]
    if end_time:
        et = end_time[:19].replace("T", " ")
        matched = [l for l in matched if l[:19] <= et]
    if keyword:
        kw = keyword.lower()
        matched = [l for l in matched if kw in l.lower()]
    total = len(matched)
    tail = matched[-lines:]
    return {"table_name": table_name, "lines": total, "content": "".join(tail)}


@router.get("/history")
def sync_history(
    table_name: str = None,
    status: str = None,
    limit: int = Query(default=50, le=200),
):
    session = SessionLocal()
    try:
        q = session.query(SyncLog).order_by(SyncLog.start_time.desc())
        if table_name:
            q = q.filter(SyncLog.table_name == table_name)
        if status:
            q = q.filter(SyncLog.status == status)
        rows = q.limit(limit).all()
        return [
            {
                "id": r.id,
                "table_name": r.table_name,
                "sync_mode": r.sync_mode,
                "status": r.status,
                "rows_fetched": r.rows_fetched,
                "rows_inserted": r.rows_inserted,
                "start_time": r.start_time.isoformat() if r.start_time else None,
                "end_time": r.end_time.isoformat() if r.end_time else None,
                "duration": (
                    (r.end_time - r.start_time).total_seconds()
                    if r.end_time and r.start_time
                    else None
                ),
                "error_message": r.error_message,
            }
            for r in rows
        ]
    finally:
        session.close()


@router.get("/checkpoints")
def checkpoints():
    session = SessionLocal()
    try:
        rows = session.query(SyncCheckpoint).all()
        return [
            {
                "table_name": r.table_name,
                "last_sync_date": r.last_sync_date.isoformat() if r.last_sync_date else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    finally:
        session.close()


@router.get("/logs/{module}")
def get_logs(
    module: str,
    lines: int = Query(default=200, le=1000),
    keyword: str = Query(default=None),
):
    log_file = os.path.join(settings.LOG_DIR, f"{module}.log")
    total, content = _read_log_file(log_file, lines=lines, keyword=keyword)
    return {"module": module, "lines": total, "content": content}


@router.get("/logs")
def list_log_files():
    log_dir = settings.LOG_DIR
    if not os.path.exists(log_dir):
        return []
    files = []
    for f in sorted(os.listdir(log_dir)):
        if f.endswith(".log"):
            path = os.path.join(log_dir, f)
            files.append({
                "name": f,
                "module": f.replace(".log", ""),
                "size_kb": round(os.path.getsize(path) / 1024, 1),
            })
    return files


@router.get("/stats")
def sync_stats():
    session = SessionLocal()
    try:
        total = session.query(SyncLog).count()
        success = session.query(SyncLog).filter(SyncLog.status.in_(["success", "completed"])).count()
        failed = session.query(SyncLog).filter(SyncLog.status == "failed").count()
        running = session.query(SyncLog).filter(SyncLog.status == "running").count()
        latest = (
            session.query(SyncLog)
            .order_by(SyncLog.start_time.desc())
            .first()
        )
        last_sync = latest.start_time.isoformat() if latest and latest.start_time else None
        checkpoint_count = session.query(SyncCheckpoint).count()
        return {
            "total_runs": total,
            "success": success,
            "failed": failed,
            "running": running,
            "last_sync": last_sync,
            "checkpoint_count": checkpoint_count,
            "task_count": len(DATA_SYNC_TASKS),
        }
    finally:
        session.close()


@router.post("/trigger/{table_name}")
def trigger_sync(table_name: str, background_tasks: BackgroundTasks,
                 mode: str = Query(default=''), max_pages: int = Query(default=0)):
    if table_name not in DATA_SYNC_TASKS:
        return {"error": f"Unknown table: {table_name}"}
    current = TASK_STATUS.get(table_name)
    if current == "running":
        return {"error": f"Task {table_name} is already running"}
    TASK_STATUS[table_name] = "queued"
    background_tasks.add_task(_run_sync_task, table_name, mode=mode or None, max_pages=max_pages or None)
    return {"status": "queued", "table_name": table_name, "mode": mode or "default"}


@router.post("/stop/{table_name}")
def stop_sync(table_name: str):
    if table_name in _running_syncs and _running_syncs[table_name].get("running"):
        _running_syncs[table_name]["cancel"] = True
        return {"status": "stopping", "table": table_name}
    session = SessionLocal()
    try:
        running = session.query(SyncLog).filter(
            SyncLog.table_name == table_name, SyncLog.status == "running"
        ).first()
        if running:
            running.status = "cancelled"
            running.end_time = datetime.now()
            running.error_message = "Cancelled by user"
            session.commit()
            TASK_STATUS[table_name] = "cancelled"
            return {"status": "stopped", "table": table_name}
    finally:
        session.close()
    return {"error": f"No running sync for {table_name}"}


@router.post("/stop-all")
def stop_all_sync():
    stopped = []
    for tbl, state in _running_syncs.items():
        if state.get("running"):
            state["cancel"] = True
            stopped.append(tbl)
    return {"status": "stopping", "tables": stopped} if stopped else {"error": "No running syncs"}


@router.get("/validate/{table_name}")
def validate_table(table_name: str):
    """Validate data coverage for a table."""
    if table_name == "sohu_jlp":
        from data.sync.sohu_jlp_sync import validate_coverage
        import psycopg2
        conn = psycopg2.connect(
            host=settings.DB_HOST, port=settings.DB_PORT,
            user=settings.DB_USER, password=settings.DB_PASSWORD,
            database=settings.DB_NAME
        )
        try:
            cur = conn.cursor()
            result = validate_coverage(cur)
            cur.close()
            return result
        finally:
            conn.close()
    elif table_name == "eastmoney_report":
        from data.sync.eastmoney_report_sync import validate_coverage
        import psycopg2
        conn = psycopg2.connect(
            host=settings.DB_HOST, port=settings.DB_PORT,
            user=settings.DB_USER, password=settings.DB_PASSWORD,
            database=settings.DB_NAME
        )
        try:
            cur = conn.cursor()
            result = validate_coverage(cur)
            cur.close()
            return result
        finally:
            conn.close()
    elif table_name == "bond_basic":
        from data.sync.bond_sync import validate_coverage
        import psycopg2
        conn = psycopg2.connect(
            host=settings.DB_HOST, port=settings.DB_PORT,
            user=settings.DB_USER, password=settings.DB_PASSWORD,
            database=settings.DB_NAME
        )
        try:
            cur = conn.cursor()
            result = validate_coverage(cur)
            cur.close()
            return result
        finally:
            conn.close()
    return {"error": f"Validation not supported for {table_name}"}
