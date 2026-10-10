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

### By-Code 增量模式（api_date_type: "code"）
- `_sync_by_code` 不再每次全量重拉：以 `sync_checkpoint` 日期减 `sync_overlap_days`（默认 3）天作为 `start_date` 传给 API（按 ann_date/trade_date 区间语义，视 API 而定；cb_share 按 publish_date 区间）。
- 任一 code 失败（客户端 3 次重试后）则本次**不推进 checkpoint**，下次运行用旧窗口自动重试；全部成功才更新为当天。
- 首次运行（无 checkpoint 且无数据）仍为全量。

### 逐日增量失败语义（防数据缺口）
- `_sync_incremental` 中某交易日失败：若此前无任何成功写入则快速中止（checkpoint 不动）；否则继续同步后续日期，结束后将 checkpoint **回滚到首个失败日前一天**并标记失败，下次运行自动重补缺口（此前失败日会被 checkpoint 越过、永久丢失）。
- cancel 异常不受影响，立即中止。

### 启动补同步（catch-up）
- `create_scheduler` 额外注册一次性任务 `sync_startup_catchup`：进程启动 90 秒后调用 `engine.sync(stale_days=2)`，**仅同步 checkpoint（无 checkpoint 则看最近一次成功同步）落后超过 2 天的表**，按 priority 顺序串行执行。
- 新鲜表只做一次 checkpoint 查询即跳过，平时重启无 API 消耗；长时间停机后重启自动追平所有缺口，不必等 cron 到点（周线/月线表尤其受益）。
- `RUNNING_SYNCS` 经 `_SYNC_LOCK` 加锁：补同步与 cron / Web 手动触发并发时，同一张表只会跑一个实例（防 `_tmp_*` 临时表互相覆盖）。
- 验证 Web 层仍必须先 stub `data.sync.scheduler.create_scheduler`（红线不变，补同步任务也在其中）。

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

## 9.5 Minute Data Sync (stk_mins_5min, baostock)

分钟数据链路详见 `MINUTE_DATA_PLAN.md`（V1.2）。与 Tushare 链路的关键差异：

- **数据源是 baostock（免费）**，非 Tushare。Tushare `stk_mins` 限流 1 次/小时，已否决。
- 任务走 `sync_func: data.sync.minute_sync.sync_nightly`（`_sync_custom` 路径），夜间 19:00 全市场从 per-code checkpoint 续采至今日（宕机多久补多久，无上限）。
- **写入统一走 `data/sync/bulk_writer.py`（COPY 化）**：`engine._write_df` 也已委托它，日线同步同样受益。单批 5 万行 ≤2s。
- baostock 特性（M0 实测）：bar time 为结束时刻（入库转起始 -5min）；vol 单位**股**（daily.vol 是手，差 100 倍）；**5min 不含收盘竞价**（末根 close ≠ daily close 属正常）；全局单连接不可多线程；停牌=空数据+err=0（推进 checkpoint）。
- `sync_code_checkpoint` 表：按股票断点（区别于 `sync_checkpoint` 表级断点）。
- 回填/收尾：`python scripts/backfill_mins.py --universe hs300 --start <日期>`；完成后 `--finalize`（逐块压缩+挂策略+按月刷 cagg）。**回填期间绝不挂压缩策略**（chunk 反复解压/重压会崩掉写入性能）。
- 压缩基准实测：30d chunk 压缩比 3.7x（10 年全市场 5min ≈ 14GB）。验收线 ≥3x。
- 环境注意：本机 PG16（原生 Windows 服务）已于 2026-10-08 安装 TimescaleDB 2.30.2 扩展（此前 setup_timescale 一直静默降级为普通 PG）；`shared_preload_libraries='timescaledb'` 已写入 postgresql.auto.conf。Timescale 2.30 API 口径：`compress_chunk`/`decompress_chunk` 是**函数**（SELECT），`refresh_continuous_aggregate` 是**过程**（CALL）；cagg 压缩配置用 `ALTER MATERIALIZED VIEW`；chunk 信息在 `timescaledb_information.dimensions.time_interval`。

## 10. Restart Procedure

修改 `config/data_sync_config.py` 后必须重启 FastAPI 才能生效。重启 90 秒后会自动补同步所有 checkpoint 落后超过 2 天的表（长时间停机后的第一次重启会触发较长的追平同步，属预期行为）。

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

### 问题 9：`code_source` 表无 `list_status` 列
**原因**: `_sync_by_code` 默认 `code_filter: "list_status = 'L'"`，但 `fx_obasic` 等非股票基础表没有此列。
**修复**: 在 config 中显式设置 `"code_filter": "1=1"`。

### 问题 10：VARCHAR 列长度不足
**原因**: 建表时 VARCHAR(20) 太短，实际 API 返回更长的值（如 `trading_hours: "Sun 17.00 - Fri 16.55"`）。
**修复**: 建表前先检查 API 实际数据长度，VARCHAR 用 50~100。已遇到：
- `fx_obasic.trading_hours`: VARCHAR(20) → 改为 VARCHAR(100)

### 问题 11：`fx_basic` API 不存在
**原因**: Tushare 外汇基础信息 API 名是 `fx_obasic`（不是 `fx_basic`）。
**修复**: 先用 `pro.fx_obasic()` 测试确认 API 名称。

### 问题 12：API 分页限制导致数据不全
**原因**: Tushare `fx_daily` API 每次最多返回 4000 行（最新数据），不传日期参数只得到近 4000 行。
**修复**: 创建自定义同步函数 `fx_daily_sync.py`，每个品种调用 2 次 API：
1. 不传日期 → 最新 4000 行
2. 传 `start_date='20000101', end_date=最早日期-1天` → 历史数据

### 问题 13：自定义同步函数 `sync_func` 路径格式
**原因**: engine 用 `rsplit('.', 1)` 分割路径，格式必须是 `module.path.func_name`（全用点号）。
**错误示例**: `data.sync.fx_daily_sync:sync_fx_daily`（冒号分隔）
**正确示例**: `data.sync.fx_daily_sync.sync_fx_daily`（点号分隔）

### 问题 14：自定义同步函数接口规范
**函数签名**: `def sync_xxx(db_conn, mode='full', max_pages=0, batch_size=50, **kwargs) -> dict`
- `db_conn`: psycopg2 原始连接（不是 SQLAlchemy 连接）
- 返回值: `{"total_rows": N, ...}` 字典
- 用 `db_conn.cursor()` 执行 SQL，不能用 `db_conn.execute(text())`
- `df.to_sql()` 需要 SQLAlchemy engine，不能用 psycopg2 连接
- 完整示例: `data/sync/fx_daily_sync.py`
