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

## 8. Logging Requirements

### Progress Logging
```python
# 每 N 条记录打印一次进度
if record_count % 1000 == 0:
    logger.info(f'Sync progress: {record_count} records, new={new_count}, errors={error_count}')

# 每 N 页打印一次进度
if page_num % 100 == 0:
    logger.info(f'Page {page_num}/{total_pages} ({page_num*100//total_pages}%), records={total_records}')
```

### Error Logging
```python
# 所有异常必须记录
try:
    process_record(rec)
except Exception as e:
    logger.error(f'Failed to process {rec["id"]}: {e}')
    # 不要吞掉异常，继续处理下一条

# 网络错误用 warning（可重试）
logger.warning(f'API timeout page {page_no} (attempt {attempt+1}): {e}')

# 数据问题用 warning
logger.warning(f'Invalid rating: {rec.get("rating")} for {ts_code}')

# 严重错误用 error
logger.error(f'Database connection failed: {e}')
```

### 日志级别规范
| 级别 | 场景 | 示例 |
|------|------|------|
| `DEBUG` | 仅调试用，生产环境不打 | 临时排查问题，代码提交前删除 |
| `INFO` | 正常进度 | 同步开始/完成、进度报告 |
| `WARNING` | 可恢复问题 | API 超时、数据格式异常、upsert 失败 |
| `ERROR` | 严重错误 | 数据库连接失败、核心功能异常 |

**注意：debug 日志仅用于临时调试，不要留在正式代码中。**

### 同步完成日志
```python
logger.info(f'Sync complete: {stats}')
# 输出: Sync complete: {total_records: 12345, new: 10000, errors: 5, years: {2020: 5000, 2021: 5345}}
```

## 9. File Checklist

When adding a new sync source:
- [ ] `config/data_sync_config.py` - Add task config
- [ ] `data/sync/xxx_sync.py` - Sync function + validate_coverage
- [ ] `data/quality/rules.py` - Quality rules
- [ ] `web/api/admin_api.py` - CLASSIFICATION_MAP + validate endpoint
- [ ] `visualization/grafana/dashboards/xxx.json` - Dashboard
- [ ] Restart uvicorn to load changes

## 10. Restart Procedure

修改 `config/data_sync_config.py` 后必须重启 FastAPI 才能生效。

### 检查是否有正在运行的 task
```python
import requests
r = requests.get('http://localhost:8088/api/sync/running', timeout=3)
running = r.json()
if running:
    print("有 task 正在运行，等待结束后再重启：", list(running.keys()))
else:
    print("无 task 运行，可以安全重启")
```

### 重启 FastAPI
```powershell
# 1. 找到 uvicorn 进程
Get-CimInstance Win32_Process -Filter "CommandLine like '%uvicorn%'" | Select-Object ProcessId, CommandLine

# 2. 杀掉旧进程
Stop-Process -Id <PID> -Force

# 3. 启动新进程
Start-Process -FilePath "C:\veighna_studio\python.exe" `
  -ArgumentList "-m uvicorn web.app:app --port 8088" `
  -WorkingDirectory "D:\code\Python\ai_quant_python" `
  -WindowStyle Hidden
```

### 验证生效
```python
import requests
r = requests.get('http://localhost:8088/admin/api/tasks', timeout=5)
tasks = [t['table_name'] for t in r.json()]
assert 'new_task' in tasks, "新 task 未出现在列表中"
```

---

## 11. Grafana Dashboard 常见问题

### 问题 1：`db has no time column` 错误
**原因**: Grafana PostgreSQL 插件默认期望 "time series" 格式，stat/table 面板没有 time 列就会报错。
**修复**: 所有非 timeseries 面板的 target 必须加 `"format": "table"`：
```json
{
  "rawQuery": true,
  "rawSql": "SELECT ...",
  "format": "table",
  "refId": "A"
}
```
timeseries 面板**不能**有 `format: table`。

### 问题 2：`syntax error at "$"` 错误
**原因**: Grafana 模板变量 `${var:csv}` / `${var:raw}` 在 PostgreSQL raw SQL 中不兼容。
**修复**: 不要在 rawSql 中使用模板变量宏。改用固定 SQL：
```sql
-- 错误写法
WHERE ts_code IN (${bond_ts_code:csv})
-- 正确写法
WHERE ts_code IN (SELECT DISTINCT ts_code FROM cb_daily)
```

### 问题 3：varchar 日期列无法比较
**原因**: 表中日期字段是 `VARCHAR(20)`，SQL 用 `::text - INTERVAL` 会报类型不匹配。
**修复**: 先 cast 为 date：
```sql
-- 错误写法
WHERE trade_date >= (SELECT MAX(trade_date))::text - INTERVAL '90 days'
-- 正确写法
WHERE trade_date::date >= (SELECT MAX(trade_date))::date - INTERVAL '90 days'
```

### 问题 4：timeseries 面板 time 列必须是 date/timestamp 类型
**原因**: `SUBSTRING(ann_date, 1, 6)` 返回 varchar（如 '202606'），Grafana 无法解析。
**修复**: 用 `TO_DATE()` 转换：
```sql
-- 错误写法
SELECT SUBSTRING(ann_date, 1, 6) AS time, ...
-- 正确写法
SELECT TO_DATE(SUBSTRING(ann_date, 1, 6), 'YYYYMM') AS time, ...
```

### 问题 5：全量同步 + TRUNCATE 导致数据丢失
**原因**: `_sync_full` 中先 TRUNCATE 再同步，如果同步被 cancel（服务器重启），数据全部丢失。
**修复**: 已移除 `_sync_table` 中的 TRUNCATE 逻辑。全量同步使用 upsert 覆盖。

### 问题 6：verify 报 `0/5 checks failed` 但 status=fail
**原因**: `SyncLog.status` 存的是 `"completed"`，但 stats 查 `"success"`。
**修复**: admin_api.py 中查询改为 `status.in_(["success", "completed"])`。

### 问题 7：verify 报 `character varying = date` 错误
**原因**: `sync_verifier._fetch_from_db` 传入 Python `date` 对象，但列是 VARCHAR。
**修复**: 改为 `d.strftime("%Y%m%d")` 传字符串。

### 问题 8：Tushare API 分页数据与 DB 日期不一致
**原因**: `cb_share` API 用 `end_date=X` 查询时返回跨多日期数据（最多 2000 行），但 DB 只存与查询日期匹配的行。
**修复**: 对这类 API 设置 `"verify_sample_size": 0` 跳过自动验证。
