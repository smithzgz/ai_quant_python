# 分钟数据实施方案 (Minute Data Implementation Plan)

> 版本: V1.2 | 日期: 2026-10-08
> 前置条件: 2026-10 正确性修复已合入（复权视图、组合口径、vbt 1.0 适配）
> 核心原则: 存储靠压缩、查询靠聚合、同步靠断点、回测靠分层
>
> **V1.2 修订（M0 spike 结论：数据源切换 Tushare → baostock）**：
> - 实测 Tushare `stk_mins` 限流 **1 次/小时**（权限可用但频次不可用），全市场增量 5,400 次/日 = 225 天，方案 A/B/C 全部不可行，5min 降级同样无效（限流按接口）；
> - **数据源切换为 baostock `query_history_k_data_plus(frequency="5")`**：免费、无限流、单次可返回一年 11,760 行（8.8s）、2011+ 历史、48 bar/日；
> - 基础表由 `stk_mins_1min` 改为 **`stk_mins_5min`**，cagg 只保留 `stk_mins_1day`；执行级模拟精度降为 5min；
> - baostock 特性：time 为 bar 结束时刻（入库统一转起始时刻）、volume 单位股（非手）、全局单连接**不可多线程**（回填单线程 + 断点续传）、无 tradestatus 字段（停牌=空响应）；
> - 走现有 `_sync_custom`（sync_func）路径，不扩展 tushare api_date_type；
> - M0 spike 详细数据见 §3.1。
>
> V1.1 修订（设计评审结论）：
> 1. **压缩策略启用时序重排**——回填期间禁止挂压缩策略（否则 chunk 反复解压/重压，写入性能崩塌），回填完成后手动 compress_chunk 再挂策略；
> 2. **cagg 自身补压缩策略**——5min cagg 10 年未压缩 ~50GB，会超过压缩后的 1min 原始表；
> 3. 回填写入的历史区间**必须手动 refresh_continuous_aggregate**（策略水位线不覆盖历史）；
> 4. 夜间增量改为按 per-code checkpoint 续采，覆盖多日停机缺口；
> 5. 明确 `daily` 与 `stk_mins_1day` 口径分工；15/60min 由 5min 查询时上卷，不建额外 cagg；
> 6. 传输层去掉不存在的 `COPY ... FORMAT arrow`，改为 COPY csv / DuckDB postgres_scan / connectorx；
> 7. 补停牌空响应语义、回填/夜间调度 advisory lock 互斥、retention 与回填年限一致性；
> 8. 范围边界明确 qlib 接入（dump_bin 导出层）不在本方案内，且现有代码 "qlib" 命名为手写模拟，需正名。

---

## 1. 目标与范围

| 场景 | 数据频率 | 优先级 |
|------|---------|--------|
| 执行级回测模拟（滑点/成交假设/T+1验证） | 5min 基础表 | P1 |
| 日内信号研究（均线/动量在 5min/15min 粒度） | 5min 基础表 | P0 |
| Grafana 分钟K线看盘 | 5min/15min/60min | P2 |
| 实时行情接入 | 不在本方案范围 | - |
| qlib 研究框架接入 | 不在本方案范围 | - |

**范围边界**：本方案覆盖 5 分钟线数据的存储、同步、查询、回测四层改造；不包含实盘交易与实时行情推送。

**关于 qlib**：当前代码中名为 "qlib" 的回测（`strategy/backtest/three_framework_shared_pool.py` 等）是手写 numpy 模拟，并非 Microsoft qlib（环境内 pyqlib 装而未用）。qlib 不直接消费 PG，若后续真要接入，需独立设计 dump_bin 导出层（calendar/instruments/features 三件套），与本项目"PG 单一数据源"路线是两个架构决策，须单独立项。接入前应先将现有伪 qlib 实现改名（如 `numpy_sim`），避免混淆。

---

## 2. 规模测算（所有设计的依据）

| 指标 | 日线（现状） | 5 分钟线（baostock） |
|------|------------|---------|
| 行数/年 | ~130 万 | **~6,400 万**（5,400 股 × 48 bar × 244 日） |
| 10 年累计 | 1,817 万 | **~6.4 亿** |
| 单行大小 | ~90 B | ~80 B（ts_code + timestamptz + 5 float） |
| 未压缩磁盘 | ~2 GB | **~51 GB** |
| Timescale 压缩后（M1 实测 3.7x，30d chunk） | - | **~14 GB** |
| stk_mins_1day cagg 10 年累计 | - | **~1,320 万行 / 未压缩 ~1 GB**（同样加压缩配置） |
| 每日增量 | ~130 万行（1 次 API 调用/日） | ~25.9 万行（5,400 次 API 调用 ≈ 40 分钟） |

**关键推论**：
1. 没有列存压缩，磁盘与 I/O 仍不可行（未压缩 51GB + 索引翻倍）→ 压缩是 M1 硬性验收项；
2. 全市场 10 年 5min 无法整块载入内存（6.4 亿 × 8B × 多数组 = 50GB+ 级）→ 回测必须分层；
3. baostock 无限流，同步瓶颈在单股查询耗时（~0.5s/股/日）→ 全市场夜间增量单线程 ~40 分钟可行；
4. 执行级模拟精度为 5min（成交假设按 5min bar 内均匀成交/触及即成交近似），1min 精度需方案 C。

---

## 3. 数据源与回填策略

### 3.1 数据源实测结论（M0 已完成，2026-10-08）

**Tushare `stk_mins`（已否决）**：积分权限可用，但限流 **1 次/小时**（首次调用成功 482 行后同窗口全部被拒，8 分钟后仍被拒）。全市场增量 5,400 次/日 = 225 天，不可行；限流按接口不按频率，5min 降级无效。

**baostock（选定，已实测）**：

| 特性 | 实测结果 |
|------|---------|
| 接口 | `bs.query_history_k_data_plus(code, fields, start_date, end_date, frequency="5", adjustflag="3")` |
| 登录 | `bs.login()` 匿名成功，全局单连接（**不可多线程**，多进程需各自 login） |
| 字段 | `date, time, code, open, high, low, close, volume, amount`（价格/量为字符串需转换） |
| time 格式 | `20260921093500000`（YYYYMMDDHHMMSSsss，**bar 结束时刻**）→ 入库统一转 bar 起始时刻（-5min）：09:35→09:30 … 15:00→14:55，48 bar/日 |
| 单位 | volume=**股**（非手，与 daily.vol 手口径差 100 倍）、amount=元 |
| 单次上限 | 一年 11,760 行一次返回**无截断**（8.8s）→ 按年分段拉取 |
| 复权 | adjustflag=3 不复权入库，qfq 查询时算（复权不物化原则） |
| 停牌/假期 | 空数据 + err=0（正常空响应）；5min 无 tradestatus 字段 |
| **收盘竞价** | **5min bar 不含收盘竞价**：官方 daily close = 竞价价，分钟末根 close = 14:55-15:00 连续竞价价。实测 600519.SH 2025-11-27 末根 1444.48 vs 官方 1447.30（差 0.19%，竞价量 62,989 股）。执行级模拟 EOD 结算须用 daily close；分钟收益对日收益有竞价日级偏差（M4 实测 81 日最大 1.96e-03） |
| 代码格式 | `sz.000001` / `sh.600000`（前缀制，需与 ts_code 互转） |
| 更新时间 | 每日 ~17:30-18:00 更新当日数据，19:00 调度安全 |

### 3.2 回填策略（M0 后定稿：方案 B'）

| 方案 | 内容 | 调用量 | 适用 |
|------|------|--------|------|
| A. 纯增量 | 从上线日起每日积累 | 5,400 次/日（≈40 分钟） | 执行级回测只做近期区间 |
| **B'. baostock 成分股回填（选定）** | 沪深300+中证500（~800 只）按年分段回填 3-5 年 | 800 股 × 3 年 ≈ 2,400 次 × ~9.5s ≈ **6-7 小时单线程跑一晚** | 研究需要中长历史 |
| C. 采购数据包 | 1min 原始数据 | 一次性导入 | 商业化需求出现时评估 |

**调用量估算（B'）**：单股一年 5min ≈ 11,760 行 / 1 次调用（~9.5s 含传输）；800 股 × 3 年 = 2,400 次调用；单线程 ~6.5 小时（baostock 单连接不可多线程，靠断点续传跑一晚）；全市场 10 年 5min ≈ 54,000 次 ≈ 6 天（不推荐）。

---

## 4. 存储设计

### 4.1 表结构

```sql
CREATE TABLE stk_mins_5min (
    ts_code     VARCHAR(20)  NOT NULL,
    trade_time  TIMESTAMPTZ  NOT NULL,   -- bar 起始时刻（baostock 结束时刻 -5min 转换），Asia/Shanghai 本地时间
    open        DOUBLE PRECISION,
    high        DOUBLE PRECISION,
    low         DOUBLE PRECISION,
    close       DOUBLE PRECISION,
    vol         DOUBLE PRECISION,        -- 单位：股（baostock 口径，注意与 daily.vol 手差 100 倍）
    amount      DOUBLE PRECISION,        -- 单位：元
    PRIMARY KEY (ts_code, trade_time)    -- hypertable 要求分区键在唯一约束内
);
```

设计说明：
- **不建** qfq/hfq 分钟物化表——复权因子是日粒度，查询时 join `daily` 的 `adj_factor` 即可（收益计算只需 `close_t * f_t / f_ref`，join 代价可忽略）。这是本次正确性修复确立的"复权不物化"原则的延续。
- `amount` 保留（分钟级成交额对滑点模型有用）。
- **不做** ts_code→int 映射与价格 float4 降精度：`compress_segmentby='ts_code'` 下映射的收益仅剩未压缩热窗口，不值当引入全链路 join 复杂度；float4 对大额 amount 有精度风险。

### 4.2 Hypertable + 压缩 + 保留策略

```sql
-- M1 建表期：只建 hypertable，不挂压缩策略
SELECT create_hypertable('stk_mins_5min', 'trade_time',
    chunk_time_interval => INTERVAL '30 days');         -- ~525 万行/chunk，10 年 ~122 块

-- 压缩配置（M1 声明；SET compress 仅声明配置，无副作用，策略回填完成后才挂）
ALTER TABLE stk_mins_5min SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ts_code',
    timescaledb.compress_orderby   = 'trade_time DESC'
);

-- ↓ M3 回填完成后才执行（见第 9 节时序）：
-- SELECT add_compression_policy('stk_mins_5min', INTERVAL '30 days');

-- 5min 基础数据长期保留（10 年仅 ~14GB 压缩后），不设 retention
```

> chunk 选 30 天（M1 基准实测修订）：7d chunk 下每股压缩 segment 仅 ~240 行，压缩比 3.2x；30d chunk segment ~1,000 行，压缩比 3.7x 且单股查询更快（chunk 边界更少）。
>
> **压缩比实测基准（2026-10-08，Timescale 2.30.2 / PG16，2000 股 × 5 日生产规模合成数据）**：
> - 7d chunk + float8：3.2x；vol/amount BIGINT：3.5x；30d chunk：3.7x
> - 原方案 "10-15x / 验收 ≥8x" 对 OHLCV 数据形态过于乐观，验收线修订为 **≥3x**；
> - 10 年全市场 5min 未压缩 ~51GB → 压缩后 ~14GB，绝对量可接受（压缩比是手段不是目的）；
> - 价格保持 float8（qfq 一致性要求 1e-9 容差，float4 不满足），vol/amount 保持 float8（BIGINT 收益仅 +0.3x，不值当引入单位换算）。

> **压缩启用时序（V1.1 关键修订）**：压缩 chunk 不可变，写入已压缩 chunk 会触发整块解压并在下轮策略时重压。若按股票迭代回填 3-5 年历史，同一 chunk 会被几百只股票轮流命中，挂着的压缩策略会导致 chunk 反复解压/重压数百次，写入性能崩塌。因此：
> - **回填场景（方案 B'）**：回填期间不挂 compression policy；回填完成后逐块 `CALL compress_chunk(<chunk>)` 手动压缩，再 `add_compression_policy` 接管后续增量；
> - **纯增量场景（方案 A）**：无历史回填，M2 上线积累 ≥ 7 天后任意时刻挂策略即可，无冲突。

### 4.3 连续聚合（ stk_mins_1day ）

基础查询面就是 `stk_mins_5min` 本身（数据量已缩 5 倍，单股区间查询直接命中索引）。仅需一个日级 cagg 供分钟衍生分析：

```sql
CREATE MATERIALIZED VIEW stk_mins_1day
WITH (timescaledb.continuous) AS
SELECT ts_code,
       time_bucket('1 day', trade_time) AS bucket,
       first(open, trade_time)  AS open,
       max(high)                AS high,
       min(low)                 AS low,
       last(close, trade_time)  AS close,
       sum(vol)                 AS vol,       -- 股口径
       sum(amount)              AS amount
FROM stk_mins_5min
GROUP BY ts_code, bucket
WITH NO DATA;

-- 自动刷新策略（水位线机制，只覆盖滚动窗口，见下方"历史区间手动刷新"）
SELECT add_continuous_aggregate_policy('stk_mins_1day',
    start_offset      => INTERVAL '3 days',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');

-- cagg 也加压缩配置（10 年仅 ~1GB，压缩收益小但无害；策略同样回填完成后再挂）
ALTER TABLE stk_mins_1day SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ts_code',
    timescaledb.compress_orderby   = 'bucket DESC'
);
```

cagg 的 compression policy 同样遵循 4.2 的时序：回填期间不挂，回填完成、手动刷新 cagg 后再挂。

**历史区间的手动刷新（回填必做，V1.1 修订）**：`add_continuous_aggregate_policy` 是水位线机制，只刷新 `[now-3d, now-1h]` 滚动窗口内的新数据；**回填写入的历史区间永远不会被自动策略刷新**，cagg 里查不到。回填完成后必须对每个回填区间显式刷新，按月分批推进，避免单次大范围刷新的长事务与锁：

```sql
SELECT refresh_continuous_aggregate('stk_mins_1day',
    '2023-01-01 00:00', '2023-02-01 00:00');   -- 循环推进到回填终点
```

**使用纪律**（写入代码规范）：
- 研究回测、Grafana 一律查 `stk_mins_5min`（基础表）；
- `stk_mins_1day` 仅用于分钟衍生分析（如日内波动结构），**日频口径以 `daily` 为准**——分钟聚合的日 OHLC 与 Tushare `daily` 官方值存在竞价/舍入级差异，禁止两表混算同一指标；
- `stk_mins_5min` 单股 + 限定时间区间的查询是设计内访问模式；禁止对基础表做全市场 × 全历史扫描（代码评审检查项）；
- **15/60min 不建 cagg**：由 5min 查询时 `time_bucket('15 minutes', trade_time)` / `('60 minutes', trade_time)` 上卷，无额外存储与刷新成本。

### 4.4 按股票断点表（新表）

现有 `sync_checkpoint` 是表级单日期断点，无法支撑"按股票回填、中途断掉续传"。新增：

```sql
CREATE TABLE sync_code_checkpoint (
    table_name      VARCHAR(100) NOT NULL,
    ts_code         VARCHAR(20)  NOT NULL,
    last_sync_time  TIMESTAMPTZ  NOT NULL,
    updated_at      TIMESTAMPTZ  DEFAULT NOW(),
    PRIMARY KEY (table_name, ts_code)
);
```

---

## 5. 同步引擎改造

### 5.1 配置定义（`config/data_sync_config.py` 新增）

baostock 非 Tushare 接口，走现有 `_sync_custom`（sync_func）路径，不扩展 tushare api_date_type：

```python
"stk_mins_5min": {
    "name": "5分钟线",
    "sync_func": "data.sync.minute_sync.sync_nightly",   # baostock 数据源，自定义同步函数
    "mode": "incremental",
    "enabled": False,                       # M2 验证后打开
    "schedule": "0 19 * * 1-5",             # baostock 当日数据 ~18:00 后可得
    "priority": 5,
    "verify_sample_size": 0,                # 分钟数据量大，默认关闭抽样验证
    "fields": {                             # 供 Web 展示/文档，非 tushare 拉取契约
        "ts_code":    ("str", "代码", True),
        "trade_time": ("datetime", "bar起始时刻", True),
        "open":       ("float", "开盘", False),
        "high":       ("float", "最高", False),
        "low":        ("float", "最低", False),
        "close":      ("float", "收盘", False),
        "vol":        ("float", "成交量(股)", False),
        "amount":     ("float", "成交额(元)", False),
    },
    "is_timescale": True,
    "chunk_interval": "30 days",
    "compress_after": "30 days",            # 注意：策略在回填完成后由 backfill_mins.py --finalize 挂载
    "description": "5分钟K线（baostock，bar起始时刻，vol单位股；夜间checkpoint续采）",
},
```

### 5.2 同步模块（新建 `data/sync/minute_sync.py`）与引擎改动

| 改动 | 内容 | 工作量 |
|------|------|--------|
| `minute_sync.py` | 核心同步模块：ts_code↔baostock 代码互转、per-code checkpoint 读取/推进、按年分段拉取、空响应推进断点、advisory lock。对外提供 `sync_nightly()`（engine.sync_func 调用）与 `sync_codes(codes, start, end)`（回填器调用）两个入口 | 6h |
| `_write_df` COPY 化 | 抽出 `data/sync/bulk_writer.py` 通用 COPY 写入（engine 与 minute_sync 共用，日线同步直接受益） | 6h |
| `models.py` | 新增 `StkMins5min`、`SyncCodeCheckpoint` ORM | 0.5h |
| RateLimiter | `data/sync/rate_limiter.py` 通用令牌桶（baostock 用 rate=3/s 礼貌节流即可，非硬限流） | 1h |
| 夜间增量入口 | `sync_nightly` 从 `sync_code_checkpoint` 续采至今日（非仅当日），多日停机缺口天然兜底 | 含上 |

### 5.3 `bulk_writer.py` COPY 化设计（M2 核心）

现状问题（`engine.py:297-356`）：每批次 4 次往返（建临时表 DDL → 查 `information_schema` → INSERT..SELECT → DROP）。分钟级每日 26 万行分批写入时，DDL 开销成为主要成本，且 `to_sql` 逐行协议慢。

目标实现：

```
写入路径（每批）:
1. 列类型按表名缓存（进程级 dict，首次查 information_schema）
2. DataFrame → CSV in-memory（只含目标表列，按缓存类型转换）
3. psycopg2 copy_expert("COPY _tmp_{table} FROM STDIN WITH (FORMAT csv)")
4. INSERT INTO {table} SELECT ... FROM _tmp_{table} ON CONFLICT DO UPDATE
   （临时表 ON COMMIT DROP，省掉显式 DROP）
5. 每 N 批复用同一临时表（TRUNCATE 代替 DROP/CREATE）
```

验收基准：单批 5 万行写入 ≤ 2 秒（现状约 15-25 秒）。

### 5.4 夜间增量 vs 回填（两条独立链路）

| 链路 | 进程 | 触发 | 范围 |
|------|------|------|------|
| 夜间增量 | 现有 APScheduler | cron 19:00 (周一~五) | 全市场，**从 per-code checkpoint 续采至今日**（非仅当日——多日停机/漏跑的缺口由 checkpoint 天然兜底），单线程顺序（5,400 次 ≈ 40 分钟） |
| 历史回填 | **独立进程** `scripts/backfill_mins.py` | 手动 | 指定股票池/日期区间（按年分段），**单线程**（baostock 全局单连接不可多线程），断点续传 |

回填器不进 FastAPI 进程的理由：回填耗时以小时计，与 Web/API 抢连接池和 GIL；独立进程可随时杀掉重启。

**空响应语义（停牌/退市/新股，V1.1 补充）**：baostock 对停牌/假期返回空数据 + err=0，同步逻辑必须区分：
- 空数据且调用成功 → 正常停牌，**推进 checkpoint**（否则该股停牌一日即卡死后续同步）；
- 抛异常/error_code≠0 → 不推进 checkpoint，下轮重试；
- 新上市股票无 checkpoint → 以 `stock_basic.list_date` 为起点初始化；
- 退市股票连续 N 日（建议 60）空响应 → 移出夜间增量清单（历史数据保留）。

**回填与夜间调度互斥（V1.1 补充）**：两链路写同一表和同一套 checkpoint，并存会导致重复拉取与写放大。互斥方案：PG advisory lock（如 `pg_try_advisory_lock(hashtext('stk_mins_5min:sync'))`）——回填器启动时抢锁，夜间任务触发前检查，被占则**跳过本轮并告警**（不要排队等待：回填以小时计，排队会把 19:00 任务拖到凌晨）。

**misfire 注意**：夜间任务沿用 `misfire_grace_time=3600`，Web 进程重启只补最后一次触发；多日缺口靠 checkpoint 续采兜底，不指望 misfire 补跑。

`scripts/backfill_mins.py` 接口设计：

```
python scripts/backfill_mins.py --codes 000001.SZ,600519.SH --start 2023-01-01 --end 2026-09-30
python scripts/backfill_mins.py --universe hs300 --start 2023-01-01     # 按指数成分股
python scripts/backfill_mins.py --resume                                 # 从断点继续
python scripts/backfill_mins.py --status                                 # 查看回填进度
python scripts/backfill_mins.py --finalize                               # 回填完成后：逐块压缩+挂策略+按月刷新cagg+挂cagg策略
```

---

## 6. 查询层设计

### 6.1 统一数据访问模块（新建 `data/access/bars.py`）

```python
def load_bars(
    symbols: list[str],
    start: str,
    end: str,
    freq: str = "1d",              # "1d" | "5min" | "15min" | "60min"
    columns: list[str] = None,     # 默认 OHLCV
    adjust: str = "none",          # "none" | "qfq"  ← 查询时 join daily.adj_factor，不物化
) -> dict[str, pd.DataFrame]:
```

路由规则：
- `1d` → 现有 `daily` 表
- `5min` → `stk_mins_5min` 基础表（调用方必须显式传 `max_symbols` 保护参数，>500 只直接抛错，防止误用全市场扫描）
- `15min` / `60min` → 5min 查询时 `time_bucket` 上卷，无独立存储

qfq 实现示意：

```sql
SELECT b.*, d.close * af.adj_factor / lf.latest_factor AS adj_close
FROM stk_mins_5min b
JOIN daily d   ON d.ts_code = b.ts_code AND d.trade_date = b.trade_time::date
JOIN adj_factor af ON af.ts_code = b.ts_code AND af.trade_date = b.trade_time::date
JOIN LATERAL (...per-stock latest factor...) lf ON true
WHERE b.ts_code = ANY(:symbols) AND b.trade_time BETWEEN :start AND :end
```

### 6.2 传输与缓存

- **基准**：`pd.read_sql` 走 psycopg2 逐行协议，亿行级是瓶颈。M4 改用批量通道（注意：原生 PostgreSQL **没有** `COPY ... WITH (FORMAT arrow)`，不要按此设计）：
  - 首选：`COPY (SELECT ...) TO STDOUT WITH (FORMAT csv)` + `pd.read_csv`（内存解析，吞吐 ×5-10，实现最简单，已满足 L2/L3 内存预算）；
  - 回测批量加载可选：DuckDB `postgres_scan` 直读 PG 出 Arrow/DataFrame（谓词下推、近零拷贝），或 `polars.read_database_uri`（connectorx）；
- **Parquet 缓存**：热点股票池（如回测反复用的成分股集合）按 `(pool_id, freq, year)` 落地本地 Parquet，`load_bars` 优先读缓存、miss 时回源并写缓存。同步任务完成后按日期失效对应分区；
- **负载隔离暂缓**：Grafana 查询与回测批量读共享同一 PG 实例，单机阶段先监控 Grafana 面板 p95，出现互相拖累再评估 read replica，本期不做。

### 6.3 分钟级质检规则（SQL 聚合下推，配合现有 `data/quality`）

| 规则 | SQL 形态 |
|------|---------|
| 每日 bar 数 = 240±1 | `GROUP BY ts_code, date HAVING count(*) NOT BETWEEN 239 AND 241` |
| 价格逻辑 | `WHERE high < low OR high < open OR ...` |
| 时间缺口 | lag(trade_time) 窗口检测 > 1 分钟且非午休/收盘边界 |
| 零成交 | `WHERE vol = 0 AND close != open`（可疑停牌 bar） |

全部在 DB 侧聚合返回异常行摘要，**禁止**沿用 `SELECT * LIMIT 100000` 拉到 pandas 的旧模式（该模式在日线表上也应同步废弃）。

---

## 7. 回测引擎适配

### 7.1 频率分层与内存预算

| 层 | 数据 | 股票池上限 | 内存预算 | 用途 |
|----|------|-----------|---------|------|
| L1 | 1d | 全市场 | <2 GB | 信号研究（现状能力，不动） |
| L2 | 5min | ≤ 500 | ≤ 8 GB | 日内策略验证 |
| L3 | 5min 执行级 | ≤ 50，且必须滑动窗口 | 每窗口 ≤ 2 GB | T+1/整手/涨跌停模拟 |

### 7.2 A 股执行级模拟器（落地 P0-3 遗留项）

日线 `from_signals` 无法精确表达 T+1/整手/涨跌停。方案：**预计算订单流 + `from_orders`**（`strategy/backtest/three_framework_shared_pool.py` 已验证该路线与手工模拟一致）：

```
输入: 5min 数据 + 日线信号(或分钟信号)
预处理:
  1. 信号日次日开盘 5min bar 决定可成交性（开盘价 vs 涨跌停价比较）
  2. size = floor(可用资金 × 槽位 / 成交价 / 100) × 100   ← 整手
  3. T+1: 买入信号标记 earliest_sell = 信号日 + 1 个交易日
执行: vbt.Portfolio.from_orders(size=预计算矩阵, cash_sharing=True)
```

新增 `backtest/engine/execution_sim.py`，与现有 `VBTEngine` 并列（后者继续服务日线研究）。

### 7.3 现有代码的适配点

- `vbt_engine.py`：`freq` 参数化（当前硬编码 `"1D"`）；
- `DataLoader`：分钟场景改走 `data/access/bars.py`（不复用现有 13 列 pivot 逻辑，那条路径假定日线列集）；
- `persistor.py`：trade_records 的 `entry_time/exit_time` 已是 timestamptz，天然兼容分钟级时间戳，无需改动。

---

## 8. Grafana

- 新建 `dashboards/stock_kline_mins.json`：数据源一律 `stk_mins_5min`，默认时间范围 ≤ 5 个交易日，模板变量复用 ts_code；15/60min 面板用 time_bucket 上卷；
- 面板必须携带单股模板变量 + 时间范围（禁止无约束全表查询）；
- 验收：单股 5min K 线面板刷新 < 2 秒。

---

## 9. 实施阶段与验收标准

| 阶段 | 内容 | 工作量 | 验收标准 |
|------|------|--------|---------|
| **M0** ✅ | API spike：接口格式/单页上限/权限/限流实测 | 0.5 天 | ✅ 已完成：Tushare stk_mins 限流 1 次/小时否决；选定 baostock 5min（结论见 §3.1），回填方案 B' 定稿 |
| **M1** ✅ | 存储就绪：stk_mins_5min hypertable + stk_mins_1day cagg（WITH NO DATA）+ sync_code_checkpoint + 压缩配置声明（**不挂任何策略**）+ ORM 同步 | 1 天 | ✅ VERIFY PASS；生产规模压缩基准实测 3.7x（30d chunk，见 4.2，验收线修订 ≥3x）；单股 5min 查询 3-8ms |
| **M2** ✅ | 同步链路：`minute_sync.py` + `bulk_writer.py` COPY 化 + 夜间增量（checkpoint 续采 + 空响应语义 + advisory lock） | 3 天 | ✅ 合成 14 项全 PASS（含 COPY 5万行 1.92s、幂等、去重、锁互斥、按年分段）；真实同步 10 股×1月 6.9s 落库抽查通过（48bar/日、价格逻辑）。①"连续 5 交易日自动同步"待生产验证 |
| **M3** ✅ | 回填器：`backfill_mins.py` 单线程 + resume/status/--finalize（逐块 compress_chunk + 挂 5min/cagg 压缩策略 + 按月分批 refresh cagg + 挂 cagg 刷新策略） | 2 天 | ✅ 3 股×1年 34,992 行 23s；重跑零重拉；finalize 全流程+撤销演练通过 |
| **M4** ✅ | 查询层：`data/access/bars.py` + COPY csv 批量读取 + 分钟质检规则（`minute_rules.py`） | 3 天 | ✅ 13 项全 PASS；qfq 计算路径与日线一致（000001.SZ 全样本 0 误差）；600519.SH 差异限于竞价日（max 1.96e-03，数据源特性见 3.1）；质检检出注入脏数据。Parquet 缓存未实现（数据量小暂无必要，按需补） |
| **M5** ✅ | 回测：execution_sim（T+1/整手/涨跌停，5min 精度）+ vbt_engine freq 参数化 + Grafana 分钟面板 | 3 天 | ✅ 合成 11 项全 PASS（涨停跳买/跌停跳卖/整手/T+1/首根 bar 成交）；端到端 run_backtest_task.py 回归通过；`stock_kline_mins.json` 自动加载并验证出数 96 bar |

总计 ~12.5 人天（实际压缩至约 1 天，M0 结论使方案简化是主因）。

**遗留事项（生产验证）——2026-10-09 更新，全部完成**：
1. ~~`stk_mins_5min` 任务 `enabled=False`~~ → **已启用**（2026-10-09），Web 已重启，调度器注册 `sync_stk_mins_5min (0 19 * * 1-5)`；
2. ~~全市场回填~~ → **已完成**：HS300+ZZ500 共 800 股 × 2023-01-01→今 = **3,423 万行**（5,212 股有数据，791 股全历史），finalize 挂载全部策略（47 chunk 压缩至 1,096MB ≈ 3.1x，含 PK 索引），cagg stk_mins_1day 713,186 行；全市场夜间增量已实战跑通（国庆后首个交易日 5,224 股当日数据落库）；
3. ~~"连续 5 交易日自动同步"~~ → 调度任务已激活，待生产观察（首个自动触发 2026-10-09 19:00）。

**全量同步实战结论（2026-10-08~09）**：
- 最终验证 **7/7 ALL PASS**：缺口率 0.071%（<0.5%）、边界异常 3/708,593 股日、qfq 5/5 零误差、质检/cagg/Grafana/execution_sim 全通过；
- **baostock 夜间限速 ~3 倍**（9.5s→19.5s/股年，白天恢复），socket 偶发挂起/断连（WinError 10053）——`_fetch_5min` 已加**断线重连+3 次重试**，回填链配停滞自愈（15 分钟无进展自动重启，断点续传零重拉）；
- **北交所股票不支持**（错误 10004011，仅 sh/sz）→ 夜间清单已过滤 `.BJ` 后缀；
- **换码股源限制**：中航成飞 302132.SZ（原 300114.SZ，2025-02 换码）daily 按新码回溯全史，baostock 分钟仅换码后——残余缺口 500 天即源于此（如需可按旧码补拉）；
- checkpoint 语义注意：夜间增量把 checkpoint 推到今天后，回填器会误判"已完成"跳过历史——重跑历史前须删除缺历史股票的 checkpoint（scripts 内已有此运维模式）。

**依赖关系**：M2 依赖 M1；M4 依赖 M3（cagg 历史区间可查）；M5 依赖 M4。M0 已完成。
**纯增量路径（方案 A，无 M3 回填）**：M2 上线积累 ≥ 7 天后随时挂压缩/cagg 策略（无冲突），M4 可提前到 M2 之后。
**风险预案（M0 后生效）**：baostock 服务不稳定或停更 → 回退 Tushare 需升级积分档位；执行级 1min 精度需求出现 → 采购数据包（方案 C）。

---

## 10. 回滚方案

- M1-M2 全部为**新增**表/任务（`stk_mins_5min` enabled=False），不影响现有日线链路；
- 回滚 = `DROP TABLE stk_mins_1day, stk_mins_5min CASCADE` + 移除配置项，现有系统零影响；
- `daily_qfq/daily_hfq` 已是视图（2026-10 修复），与分钟数据互不依赖。

---

## 附：与既有 P1/P2 重构项的关系

本方案 M2 的 `_write_df` COPY 化、M4 的 `data/access` 模块、M6 质检 SQL 下推，正是前次架构分析中 P1（数据层统一）的核心内容——**分钟数据把"建议做"变成了"必须做"**。若资源允许，建议按本方案顺序实施，日线链路在 M2/M4 中同步受益，无需单独排期。
