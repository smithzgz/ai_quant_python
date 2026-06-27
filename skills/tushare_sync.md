# Tushare Sync Skill

## Overview
A-share quantitative data sync system using Tushare API + custom sources (Sohu JLP, Eastmoney). PostgreSQL 16 + TimescaleDB backend, FastAPI admin UI, Grafana dashboards.

## Architecture
```
config/data_sync_config.py    # Task definitions
data/sync/engine.py           # Sync engine (tushare + custom)
data/sync/scheduler.py        # APScheduler background tasks
web/api/admin_api.py          # Admin API + classification
web/static/admin.html         # Admin UI
visualization/grafana/        # Grafana dashboards
data/quality/rules.py         # Quality check rules
```

## 1. Task Configuration

### Standard Tushare Task
```python
"task_name": {
    "name": "显示名称",
    "api": "tushare_api_name",           # Tushare API method
    "mode": "incremental",               # once | incremental | full
    "date_field": "trade_date",          # Date field for incremental sync
    "schedule": "0 18 * * 1-5",          # Cron schedule
    "priority": 2,                       # Lower = higher priority
    "oldest_date": "19910101",           # Start date
    "api_date_type": "single",           # single | code | none
    "verify_sample_size": 5,             # Post-sync verification samples
    "fields": {
        "ts_code": ("str", "TS代码", True),   # (type, description, is_pk)
        "trade_date": ("date", "日期", True),
        "close": ("float", "收盘", False),
    },
    "is_timescale": True,                # TimescaleDB hypertable
    "chunk_interval": "7 days",          # TimescaleDB chunk interval
    "compress_after": "90 days",         # TimescaleDB compression
    "quality_rules": ["no_null_price"],  # Quality rules
}
```

### Custom Sync Task (non-Tushare)
```python
"custom_task": {
    "name": "显示名称",
    "api": "custom_api",
    "mode": "full",
    "schedule": "0 9 * * *",
    "priority": 20,
    "verify_sample_size": 5,
    "sync_func": "module.path.sync_function",  # Custom sync function
    "max_pages": 0,                            # 0=all
    "batch_size": 500,
    "quality_rules": ["rule_name"],
    "fields": {...},
    "is_timescale": False,
}
```

### Custom Sync Function Signature
```python
def sync_xxx(db_conn, mode: str = 'full', max_pages: int = 0,
             batch_size: int = 50,
             mode_override: str = None, max_pages_override: int = None) -> dict:
    """
    Args:
        db_conn: psycopg2 connection (NOT SQLAlchemy)
        mode: 'full' or 'incremental'
        max_pages: Max pages (0=all)
        mode_override: Override mode from engine
        max_pages_override: Override max_pages from engine
    Returns:
        dict with total_records, new, errors, etc.
    """
```

## 2. Common Pitfalls & Fixes

### API Pagination
- **Sohu JLP**: Requires ALL Form 1 hidden fields (`lastQuery`, `query.due`, `query.secName`, etc.)
- **Eastmoney**: Standard pagination, but check `hits` vs `data` length
- **Tushare**: Rate limit 200 calls/min, use `time.sleep(0.3)`

### Data Type Issues
- Empty string → `None` for INTEGER/DATE columns
- `rating_change` (SMALLINT): empty string causes conversion error
- Use `_safe_float()` / `_safe_int()` helpers

### Transaction Handling
- After error: `cursor.connection.rollback()` required
- Batch upsert: commit every N records, not per-record
- Use `ON CONFLICT ... DO UPDATE` for upserts

### Network Issues
- DNS timeout: retry logic with backoff
- Partial sync: use `start_page` to resume
- API may skip years silently (e.g., Eastmoney 2025)

### Function Signature Mismatch
- Engine calls: `sync_func(db_conn, mode=..., max_pages=..., batch_size=...)`
- Ensure custom functions accept these params

## 3. Data Validation

### Coverage Check Function
```python
def validate_coverage(cursor) -> dict:
    cursor.execute("""
        SELECT EXTRACT(YEAR FROM date_col)::int AS yr,
               EXTRACT(MONTH FROM date_col)::int AS mo,
               COUNT(*) AS cnt
        FROM table_name GROUP BY yr, mo ORDER BY yr, mo
    """)
    # Check: yearly counts, missing months, gaps
    return {'yearly': {...}, 'gaps': [...], 'total_records': N}
```

### Quality Rules (data/quality/rules.py)
```python
QUALITY_RULES = {
    "rule_name": {
        "name": "规则描述",
        "check_cols": ["column"],
        "threshold": 10000,      # For range checks
        "valid_values": [...],   # For enum checks
        "level": "warn",        # warn | fail
    },
}
```

### Admin API Validation
```python
@router.get("/validate/{table_name}")
def validate_table(table_name: str):
    if table_name == "xxx":
        from data.sync.xxx_sync import validate_coverage
        # ... return coverage report
```

## 4. Admin UI

### Classification Map (web/api/admin_api.py)
```python
CLASSIFICATION_MAP = {
    "table_name": "分类名称",
}
CLASSIFICATION_ORDER = ["基础信息", "行情数据", ..., "分类名称"]
```

### Dashboard API Response
```json
{
    "tables": [{
        "table_name": "xxx",
        "name": "显示名称",
        "classification": "分类",
        "mode": "incremental",
        "row_count": 12345,
        "last_sync_time": "2026-01-01T00:00:00",
        "checkpoint_date": "2026-01-01",
        "fields": {"col": {"type": "float", "desc": "描述", "pk": false}}
    }],
    "classifications": ["基础信息", ...]
}
```

## 5. Grafana Dashboard

### Panel Structure (consistent layout)
1. **Row**: Source Name
2. **6 stat panels**: Unique Stocks, Total Records, Latest Date, Unique Brokers, Unique Analysts, Unique Industries
3. **Timeseries**: Recommendations/Reports by Date
4. **BarGauge**: Top Brokers (30d)
5. **BarGauge**: Top Industries (30d)
6. **Timeseries**: Trend (target upside / rating trend)
7. **Row**: Details
8. **Table**: All Records

### Template Variables
```json
{
    "name": "ts_code",
    "label": "Stock (Source)",
    "query": "SELECT ts_code || ' - ' || name AS __text, ts_code AS __value FROM table GROUP BY ts_code, name",
    "includeAll": true,
    "multi": true
}
```

### Datasource UID
- PostgreSQL: `bfpbii1tm9ou8c`
- Use same UID for all panels

## 6. Sync Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| `once` | Sync once, never again | trade_cal, stock_basic |
| `incremental` | From last checkpoint | daily, daily_basic, adj_factor |
| `full` | Full resync | fina_indicator, sohu_jlp, eastmoney |

### Incremental Mode Logic
1. Get checkpoint date from `sync_checkpoint` table
2. Fetch data from checkpoint date to now
3. Upsert (INSERT ON CONFLICT DO UPDATE)
4. Update checkpoint

### Full Mode Logic
1. Truncate or upsert all data
2. No checkpoint (or reset checkpoint)

## 7. Debugging

### Check Task Status
```bash
curl http://localhost:8088/admin/api/dashboard
curl http://localhost:8088/admin/api/task/{table_name}
```

### Manual Sync Test
```python
import psycopg2
from config.settings import settings
from data.sync.xxx_sync import sync_xxx

conn = psycopg2.connect(host=settings.DB_HOST, ...)
result = sync_xxx(conn, mode='full', max_pages=10)
print(result)
```

### Coverage Check
```python
from data.sync.xxx_sync import validate_coverage
cur = conn.cursor()
print(validate_coverage(cur))
```

## 8. File Checklist

When adding a new sync source:
- [ ] `config/data_sync_config.py` - Add task config
- [ ] `data/sync/xxx_sync.py` - Sync function + validate_coverage
- [ ] `data/quality/rules.py` - Quality rules
- [ ] `web/api/admin_api.py` - CLASSIFICATION_MAP + validate endpoint
- [ ] `visualization/grafana/dashboards/xxx.json` - Dashboard
- [ ] Restart uvicorn to load changes
