# -*- coding: utf-8 -*-
from fastapi import APIRouter
from data.sync.engine import SyncEngine
from data.database.connection import SessionLocal
from data.database.models import SyncLog
from config.data_sync_config import DATA_SYNC_TASKS
from threading import Lock
from typing import Dict, Any

router = APIRouter()

_running_syncs: Dict[str, Dict[str, Any]] = {}
_running_lock = Lock()


@router.post("/{table_name}")
def sync_table(table_name: str, mode: str = None):
    if table_name not in DATA_SYNC_TASKS:
        return {"error": f"Unknown table: {table_name}"}
    
    with _running_lock:
        if table_name in _running_syncs and _running_syncs[table_name]["running"]:
            return {"error": f"Sync already running for {table_name}"}
        _running_syncs[table_name] = {"running": True, "cancel": False}
    
    engine = SyncEngine()
    try:
        engine.sync(table_name=table_name, mode=mode, cancel_check=lambda: _running_syncs.get(table_name, {}).get("cancel", False))
        return {"status": "completed", "table": table_name}
    except Exception as e:
        return {"status": "failed", "table": table_name, "error": str(e)}
    finally:
        with _running_lock:
            if table_name in _running_syncs:
                _running_syncs[table_name]["running"] = False


@router.post("/all")
def sync_all(mode: str = None):
    with _running_lock:
        for tbl in DATA_SYNC_TASKS:
            if _running_syncs.get(tbl, {}).get("running"):
                return {"error": f"Sync already running for {tbl}"}
        for tbl in DATA_SYNC_TASKS:
            _running_syncs[tbl] = {"running": True, "cancel": False}
    
    engine = SyncEngine()
    try:
        engine.sync(mode=mode, cancel_check=lambda: any(v.get("cancel", False) for v in _running_syncs.values()))
        return {"status": "completed"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}
    finally:
        with _running_lock:
            for tbl in DATA_SYNC_TASKS:
                if tbl in _running_syncs:
                    _running_syncs[tbl]["running"] = False


@router.post("/stop/{table_name}")
def stop_sync(table_name: str):
    with _running_lock:
        if table_name in _running_syncs and _running_syncs[table_name]["running"]:
            _running_syncs[table_name]["cancel"] = True
            return {"status": "stopping", "table": table_name}
        return {"error": f"No running sync for {table_name}"}


@router.post("/stop-all")
def stop_all_sync():
    with _running_lock:
        stopped = []
        for tbl, state in _running_syncs.items():
            if state.get("running"):
                state["cancel"] = True
                stopped.append(tbl)
        return {"status": "stopping", "tables": stopped} if stopped else {"error": "No running syncs"}


@router.get("/running")
def get_running_syncs():
    with _running_lock:
        return {tbl: state["running"] for tbl, state in _running_syncs.items() if state.get("running")}


@router.get("/status")
def sync_status():
    session = SessionLocal()
    try:
        logs = session.query(SyncLog).order_by(SyncLog.start_time.desc()).limit(20).all()
        return [
            {
                "table_name": l.table_name,
                "sync_mode": l.sync_mode,
                "status": l.status,
                "rows_inserted": l.rows_inserted,
                "start_time": str(l.start_time),
                "end_time": str(l.end_time) if l.end_time else None,
                "error_message": l.error_message,
            }
            for l in logs
        ]
    finally:
        session.close()


@router.get("/tables")
def list_tables():
    return [
        {"name": k, "description": v.get("name", ""), "mode": v.get("mode", ""),
         "enabled": v.get("enabled", True)}
        for k, v in DATA_SYNC_TASKS.items()
    ]
