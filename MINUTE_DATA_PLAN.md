# 分钟数据实施方案 (Minute Data Implementation Plan)

> 版本: V1.1 | 日期: 2026-10-07
> 前置条件: 2026-10 正确性修复已合入（复权视图、组合口径、vbt 1.0 适配）
> 核心原则: 存储靠压缩、查询靠聚合、同步靠断点、回测靠分层
>
> V1.1 修订（设计评审结论）：
> 1. **压缩策略启用时序重排**——回填期间禁止挂压缩策略（否则 chunk 反复解压/重压，写入性能崩塌），回填完成后手动 compress_chunk 再挂策略；
> 2. **cagg 自身补压缩策略**——5min cagg 10 年未压缩 ~50GB，会超过压缩后的 1min 原始表；
> 3. 回填写入的历史区间**必须手动 refresh_continuous_aggregate**（策略水位线不覆盖历史）；
> 4. 夜间增量改为按 per-code checkpoint 续采，覆盖多日停机缺口；
> 5. 明确 `daily` 与 `stk_mins_1day` 口径分工；15/60min 由 5min cagg 查询时上卷，不建额外 cagg；
> 6. 传输层去掉不存在的 `COPY ... FORMAT arrow`，改为 COPY csv / DuckDB postgres_scan / connectorx；
> 7. 补停牌空响应语义、回填/夜间调度 advisory lock 互斥、retention 与回填年限一致性；
> 8. 范围边界明确 qlib 接入（dump_bin 导出层）不在本方案内，且现有代码 "qlib" 命名为手写模拟，需正名。

---

## 1. 目标与范围

| 场景 | 数据频率 | 优先级 |
|------|---------|--------|
| 执行级回测模拟（滑点/成交假设/T+1验证） | 1min 原始 | P1 |
| 日内信号研究（均线/动量在 5min/15min 粒度） | 5min 连续聚合 | P0 |
| Grafana 分钟K线看盘 | 5min/15min/60min | P2 |
| 实时行情接入 | 不在本方案范围 | - |
| qlib 研究框架接入 | 不在本方案范围 | - |

**范围边界**：本方案覆盖 1 分钟线数据的存储、同步、查询、回测四层改造；不包含实盘交易与实时行情推送。

**关于 qlib**：当前代码中名为 "qlib" 的回测（`strategy/backtest/three_framework_shared_pool.py` 等）是手写 numpy 模拟，并非 Microsoft qlib（环境内 pyqlib 装而未用）。qlib 不直接消费 PG，若后续真要接入，需独立设计 dump_bin 导出层（calendar/instruments/features 三件套），与本项目"PG 单一数据源"路线是两个架构决策，须单独立项。接入前应先将现有伪 qlib 实现改名（如 `numpy_sim`），避免混淆。

---

## 2. 规模测算（所有设计的依据）

| 指标 | 日线（现状） | 1 分钟线 |
|------|------------|---------|
| 行数/年 | ~130 万 | **~3.2 亿**（5,400 股 × 240 bar × 244 日） |
| 10 年累计 | 1,817 万 | **~32 亿** |
| 单行大小 | ~90 B | ~80 B（ts_code + timestamptz + 5 float） |
| 未压缩磁盘 | ~2 GB | **~260 GB** |
| Timescale 压缩后（预期 10-15x） | - | **~20-30 GB** |
| 5min cagg 10 年累计 | - | **~6.4 亿行 / 未压缩 ~50 GB**（cagg 同样必须压缩，见 4.3） |
| 每日增量 | ~130 万行（1 次 API 调用/日） | ~130 万行（5,400 次 API 调用） |

**关键推论**：
1. 没有列存压缩，这条产品线在磁盘和 I/O 上不可行 → 压缩是 M1 硬性验收项；
2. 全市场 10 年 1min 原始数据无法整块载入内存（32 亿 × 8B × 多数组 = TB 级）→ 回测必须分层；
3. 每日增量行数与日线相同，但 API 调用次数 ×5,400 → 同步层瓶颈在调用次数而非数据量。

---

## 3. 数据源与回填策略

### 3.1 Tushare 接口特性（M0 须实测确认）

| 特性 | 预期行为 | 确认方式 |
|------|---------|---------|
| 接口 | `stk_mins(ts_code, freq='1min', start_date, end_date)` 或 `pro_bar(freq='min')` | M0 spike 实测 |
| 拉取粒度 | 单只股票 × 时间区间，分页返回 | M0 spike 实测 |
| 单次返回上限 | 数千 bar/次（随积分档位变化） | M0 spike 实测 |
| 积分要求 | 分钟线需要较高积分档位 | 查 Tushare 文档/账户页 |
| 时间格式 | `20260108 09:31:00` 字符串 | M0 spike 实测 |

> **M0 spike（0.5 天）**：写一个 20 行的脚本拉一只股票 3 天数据，确认：返回格式、单页上限、积分权限、限流阈值。**本方案后续所有估算依赖这四个数字。**

### 3.2 回填策略三选一（M1 前必须决策）

| 方案 | 内容 | API 调用量 | 适用 |
|------|------|-----------|------|
| **A. 纯增量（推荐起步）** | 从上线日起每日积累，不回填历史 | 5,400 次/日 | 执行级回测只做近期区间 |
| B. 成分股回填 | 沪深300+中证500（~800 只）回填 3-5 年 | ~800 股 × N 页/股 | 研究需要中长历史 |
| C. 采购数据包 | Tushare 离线数据/高积分档位 | 一次性导入 | 需要全市场长历史 |

**推荐路径**：A 起步 → 按研究需要局部升级 B（按股票池增量扩展，回填器天然支持子集）→ C 仅在商业化需求出现时评估。

**调用量估算**（方案 B，800 只 × 3 年）：
- 单股 3 年 1min ≈ 17.5 万 bar；按单页 2,000 bar → ~88 次/股
- 800 股 × 88 次 ≈ 7 万次调用；限流 3 次/秒 → **~6.5 小时纯 API 时间**（可行，跑一晚）
- 全市场 10 年（32 亿 bar）≈ 160 万次调用 → **~6 天**（不推荐，且积分档位大概率不够）

---

## 4. 存储设计

### 4.1 表结构

```sql
CREATE TABLE stk_mins_1min (
    ts_code     VARCHAR(20)  NOT NULL,
    trade_time  TIMESTAMPTZ  NOT NULL,   -- 统一 Asia/Shanghai 本地时间写入，timestamptz 存储
    open        DOUBLE PRECISION,
    high        DOUBLE PRECISION,
    low         DOUBLE PRECISION,
    close       DOUBLE PRECISION,
    vol         DOUBLE PRECISION,
    amount      DOUBLE PRECISION,
    PRIMARY KEY (ts_code, trade_time)    -- hypertable 要求分区键在唯一约束内
);
```

设计说明：
- **不建** qfq/hfq 分钟物化表——复权因子是日粒度，查询时 join `daily` 的 `adj_factor` 即可（收益计算只需 `close_t * f_t / f_ref`，join 代价可忽略）。这是本次正确性修复确立的"复权不物化"原则的延续。
- `amount` 保留（分钟级成交额对滑点模型有用）。
- **不做** ts_code→int 映射与价格 float4 降精度：`compress_segmentby='ts_code'` 下映射的收益仅剩未压缩热窗口（~650 万行），不值当引入全链路 join 复杂度；float4 对大额 amount 有精度风险。

### 4.2 Hypertable + 压缩 + 保留策略

```sql
-- M1 建表期：只建 hypertable，不挂压缩策略
SELECT create_hypertable('stk_mins_1min', 'trade_time',
    chunk_time_interval => INTERVAL '7 days');          -- ~600-900 万行/chunk，10 年 ~520 块

-- 压缩配置（M1 声明；SET compress 仅声明配置，无副作用，策略回填完成后才挂）
ALTER TABLE stk_mins_1min SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ts_code',
    timescaledb.compress_orderby   = 'trade_time DESC'
);

-- ↓ M3 回填完成后才执行（见第 9 节时序）：
-- SELECT add_compression_policy('stk_mins_1min', INTERVAL '7 days');

-- 可选：1min 原始数据保留 5 年，更久只留 cagg
-- 注意：retention 年限必须 ≥ 回填年限，否则回填进来的历史数据会被策略直接删除
-- SELECT add_retention_policy('stk_mins_1min', INTERVAL '5 years');
```

> chunk 选 7 天而非 PLAN.md 旧稿的 1 天：1 天块 10 年产生 2,400+ 块，跨块查询规划开销大；7 天块行数（~600-900 万）仍在 Timescale 推荐区间。

> **压缩启用时序（V1.1 关键修订）**：压缩 chunk 不可变，写入已压缩 chunk 会触发整块解压（~600-900 万行）并在下轮策略时重压。若按股票迭代回填 3-5 年历史，同一 chunk 会被几百只股票轮流命中，挂着的压缩策略会导致 chunk 反复解压/重压数百次，写入性能崩塌（远超 §3.2 估算的 1.6h 写入时间）。因此：
> - **回填场景（方案 B/C）**：回填期间不挂 compression policy；回填完成后逐块 `CALL compress_chunk(<chunk>)` 手动压缩，再 `add_compression_policy` 接管后续增量；
> - **纯增量场景（方案 A）**：无历史回填，M2 上线积累 ≥ 7 天后任意时刻挂策略即可，无冲突。

### 4.3 连续聚合（本方案收益最大的单项）

```sql
CREATE MATERIALIZED VIEW stk_mins_5min
WITH (timescaledb.continuous) AS
SELECT ts_code,
       time_bucket('5 minutes', trade_time) AS bucket,
       first(open, trade_time)  AS open,
       max(high)                AS high,
       min(low)                 AS low,
       last(close, trade_time)  AS close,
       sum(vol)                 AS vol,
       sum(amount)              AS amount
FROM stk_mins_1min
GROUP BY ts_code, bucket
WITH NO DATA;

SELECT add_continuous_aggregate_policy('stk_mins_5min',
    start_offset      => INTERVAL '3 days',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');

-- cagg 自身也必须压缩（V1.1 修订）：
-- 5min cagg 10 年 ~6.4 亿行、未压缩 ~50GB，会超过压缩后的 1min 原始表（20-30GB），成为全库最大的表
ALTER TABLE stk_mins_5min SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ts_code',
    timescaledb.compress_orderby   = 'bucket DESC'
);
```

同样模式建 `stk_mins_1day`，并同样加压缩配置（供跨周期研究复用，避免每次从 1min 聚合）。cagg 的 compression policy 同样遵循 4.2 的时序：回填期间不挂，回填完成、手动刷新 cagg 后再挂。

**历史区间的手动刷新（回填必做，V1.1 修订）**：`add_continuous_aggregate_policy` 是水位线机制，只刷新 `[now-3d, now-1h]` 滚动窗口内的新数据；**回填写入的历史区间永远不会被自动策略刷新**，cagg 里查不到。回填完成后必须对每个回填区间显式刷新，按月分批推进，避免单次大范围刷新的长事务与锁：

```sql
SELECT refresh_continuous_aggregate('stk_mins_5min',
    '2023-01-01 00:00', '2023-02-01 00:00');   -- 循环推进到回填终点
```

**使用纪律**（写入代码规范）：
- 研究回测、Grafana 一律查 `stk_mins_5min` / `stk_mins_1day`；
- `stk_mins_1min` 原始表仅两类消费者：执行级模拟器、cagg 刷新；
- 禁止对 1min 表做全市场扫描（代码评审检查项）；
- **日频口径以 `daily` 为准**：分钟聚合的日 OHLC 与 Tushare `daily` 官方值存在竞价/舍入级差异。回测、绩效、Grafana 日线面板一律查 `daily`；`stk_mins_1day` 仅用于分钟衍生分析（如日内波动结构），禁止两表混算同一指标；
- **15/60min 不建额外 cagg**：由 5min cagg 查询时 `time_bucket('15 minutes', bucket)` / `('60 minutes', bucket)` 二次上卷，无额外存储与刷新成本。

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

```python
"stk_mins_1min": {
    "name": "1分钟线",
    "api": "stk_mins",
    "mode": "incremental",
    "enabled": False,                       # M2 验证后打开
    "api_date_type": "code_range",          # 新模式：按股票+区间
    "params": {"freq": "1min"},
    "date_field": "trade_time",
    "schedule": "0 19 * * 1-5",             # 收盘后
    "priority": 5,
    "fields": {
        "ts_code":    ("str", "代码", True),
        "trade_time": ("datetime", "时间", True),   # 新字段类型
        "open":       ("float", "开盘", False),
        "high":       ("float", "最高", False),
        "low":        ("float", "最低", False),
        "close":      ("float", "收盘", False),
        "vol":        ("float", "成交量", False),
        "amount":     ("float", "成交额", False),
    },
    "is_timescale": True,
    "chunk_interval": "7 days",
    "compress_after": "7 days",
    "verify_sample_size": 0,                # 分钟数据量大，默认关闭抽样验证
},
```

### 5.2 `engine.py` 改动点

| 改动 | 内容 | 工作量 |
|------|------|--------|
| `_convert_dates` | 新增 `datetime` 类型分支：`pd.to_datetime(df[col], format="%Y%m%d %H:%M:%S")` | 0.5h |
| 新增 `_sync_by_code_range` | 按股票循环：读 `sync_code_checkpoint` → 区间分页拉取 → 批量写 → 更新断点。复用现有 `cancel_check` 模式 | 4h |
| `_write_df` COPY 化 | 见 5.3（这是分钟数据的前置硬条件，日线同步也直接受益） | 6h |
| 限流器 | `tushare_client.py` 固定 `sleep(0.35)` 改为令牌桶（`RateLimiter(rate)` 类，按 M0 实测阈值配置），多线程回填共享同一实例 | 2h |

### 5.3 `_write_df` COPY 化设计（M2 核心）

现状问题：每批次 4 次往返（建临时表 DDL → 查 `information_schema` → INSERT..SELECT → DROP）。分钟级每日 130 万行分批写入时，DDL 开销成为主要成本，且 `to_sql` 逐行协议慢。

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
| 夜间增量 | 现有 APScheduler | cron 19:00 | 全市场，**从 per-code checkpoint 续采至今日**（非仅当日——多日停机/漏跑的缺口由 checkpoint 天然兜底），单线程顺序（5,400 次 ≈ 32 分钟，无需并发） |
| 历史回填 | **独立进程** `scripts/backfill_mins.py` | 手动 | 指定股票池/日期区间，3-5 线程共享限流器，断点续传 |

回填器不进 FastAPI 进程的理由：回填耗时以小时计，与 Web/API 抢连接池和 GIL；独立进程可随时杀掉重启。

**空响应语义（停牌/退市/新股，V1.1 补充）**：Tushare 对停牌股返回空 DataFrame，同步逻辑必须区分：
- 空 DataFrame 且调用成功 → 正常停牌，**推进 checkpoint**（否则该股停牌一日即卡死后续同步）；
- 抛异常/超限 → 不推进 checkpoint，下轮重试；
- 新上市股票无 checkpoint → 以 `stock_basic.list_date` 为起点初始化；
- 退市股票连续 N 日（建议 60）空响应 → 移出夜间增量清单（历史数据保留）。

**回填与夜间调度互斥（V1.1 补充）**：两链路写同一表和同一套 checkpoint，并存会导致重复拉取与写放大。互斥方案：PG advisory lock（如 `pg_try_advisory_lock(hashtext('stk_mins_1min:sync'))`）——回填器启动时抢锁，夜间任务触发前检查，被占则**跳过本轮并告警**（不要排队等待：回填以小时计，排队会把 19:00 任务拖到凌晨）。

**misfire 注意**：夜间任务沿用 `misfire_grace_time=3600`，Web 进程重启只补最后一次触发；多日缺口靠 checkpoint 续采兜底，不指望 misfire 补跑。

`scripts/backfill_mins.py` 接口设计：

```
python scripts/backfill_mins.py --codes 000001.SZ,600519.SH --start 2023-01-01 --end 2026-09-30
python scripts/backfill_mins.py --universe hs300 --start 2023-01-01     # 按指数成分股
python scripts/backfill_mins.py --resume                                 # 从断点继续
python scripts/backfill_mins.py --status                                 # 查看回填进度
```

---

## 6. 查询层设计

### 6.1 统一数据访问模块（新建 `data/access/bars.py`）

```python
def load_bars(
    symbols: list[str],
    start: str,
    end: str,
    freq: str = "1d",              # "1d" | "5min" | "1min"
    columns: list[str] = None,     # 默认 OHLCV
    adjust: str = "none",          # "none" | "qfq"  ← 查询时 join daily.adj_factor，不物化
) -> dict[str, pd.DataFrame]:
```

路由规则：
- `1d` → 现有 `daily` 表
- `5min` → `stk_mins_5min` cagg
- `1min` → `stk_mins_1min` 原始表（调用方必须显式传 `max_symbols` 保护参数，>50 只直接抛错，防止误用全市场扫描）

qfq 实现示意：

```sql
SELECT b.*, d.close * af.adj_factor / lf.latest_factor AS adj_close
FROM stk_mins_5min b
JOIN daily d   ON d.ts_code = b.ts_code AND d.trade_date = b.bucket::date
JOIN adj_factor af ON af.ts_code = b.ts_code AND af.trade_date = b.bucket::date
JOIN LATERAL (...per-stock latest factor...) lf ON true
WHERE b.ts_code = ANY(:symbols) AND b.bucket BETWEEN :start AND :end
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
| L2 | 5min cagg | ≤ 500 | ≤ 8 GB | 日内策略验证 |
| L3 | 1min 原始 | ≤ 50，且必须滑动窗口 | 每窗口 ≤ 2 GB | 执行级模拟 |

### 7.2 A 股执行级模拟器（落地 P0-3 遗留项）

日线 `from_signals` 无法精确表达 T+1/整手/涨跌停。方案：**预计算订单流 + `from_orders`**（`strategy/backtest/three_framework_shared_pool.py` 已验证该路线与手工模拟一致）：

```
输入: 1min 数据 + 日线信号(或分钟信号)
预处理:
  1. 信号日次日开盘分钟 bar 决定可成交性（开盘价 vs 涨跌停价比较）
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

- 新建 `dashboards/stock_kline_mins.json`：数据源一律 `stk_mins_5min`，默认时间范围 ≤ 5 个交易日，模板变量复用 ts_code；
- **禁止**面板直查 `stk_mins_1min`（除非单股 + ≤ 3 日范围）；
- 验收：单股 5min K 线面板刷新 < 2 秒。

---

## 9. 实施阶段与验收标准

| 阶段 | 内容 | 工作量 | 验收标准 |
|------|------|--------|---------|
| **M0** | API spike：接口格式/单页上限/积分权限/限流实测 | 0.5 天 | 四个未知数落表；回填方案 A/B/C 定稿 |
| **M1** | 存储就绪：建表 + hypertable + 5min/1day cagg（WITH NO DATA）+ 断点表 + 压缩配置声明（**不挂任何策略**） | 1 天 | 灌入 10 只股 × 1 个月数据后手动 compress_chunk：压缩比 ≥ 8x；手动 refresh 后单股 5min 查询 < 100ms |
| **M2** | 同步链路：`code_range` 模式 + `_write_df` COPY 化 + 令牌桶限流 + 夜间增量（checkpoint 续采 + 空响应语义 + advisory lock） | 3 天 | ① 5 只股连续 5 交易日自动同步成功；② COPY 路径单批 5 万行 ≤ 2s；③ 中断后断点续传正确；④ 停牌股空响应正确推进 checkpoint |
| **M3** | 回填器：`backfill_mins.py` 多线程 + resume/status；回填完成后**逐块 compress_chunk + 挂 1min/cagg 压缩策略 + 按月分批 refresh cagg + 挂 cagg 刷新策略** | 2 天 | 按选定方案完成回填；杀进程重启零重拉；限流零超限告警；回填区间在 5min cagg 可查且与手工聚合抽样一致 |
| **M4** | 查询层：`data/access/bars.py` + Parquet 缓存 + COPY csv 批量读取 + 分钟质检规则 | 3 天 | load_bars 三频率路由正确；qfq 口径与日线一致（同一股票同日收益误差 < 1e-9）；质检规则检出注入的脏数据 |
| **M5** | 回测：execution_sim（T+1/整手/涨跌停）+ Grafana 分钟面板 | 3 天 | 单股 1 年 1min 执行级回测跑通；成交价全部落在涨跌停边界内；整手约束零违反；面板 < 2s |

总计 ~12.5 人天（不含 M3 回填的机器运行时间）。

**依赖关系**：M2 依赖 M1；M4 依赖 M3（cagg 历史区间可查）；M5 依赖 M4。M0 与 M1 可并行。
**纯增量路径（方案 A，无 M3 回填）**：M2 上线积累 ≥ 7 天后随时挂压缩/cagg 策略（无冲突），M4 可提前到 M2 之后。
**风险预案**：若 M0 发现积分档位不足 → 降级为 5min 粒度（数据量 ×1/5，API 调用 ×1/5，架构不变，仅 cagg 层减少一级）。

---

## 10. 回滚方案

- M1-M2 全部为**新增**表/任务（`stk_mins_1min` enabled=False），不影响现有日线链路；
- 回滚 = `DROP TABLE stk_mins_1min CASCADE` + 移除配置项，现有系统零影响；
- `daily_qfq/daily_hfq` 已是视图（2026-10 修复），与分钟数据互不依赖。

---

## 附：与既有 P1/P2 重构项的关系

本方案 M2 的 `_write_df` COPY 化、M4 的 `data/access` 模块、M6 质检 SQL 下推，正是前次架构分析中 P1（数据层统一）的核心内容——**分钟数据把"建议做"变成了"必须做"**。若资源允许，建议按本方案顺序实施，日线链路在 M2/M4 中同步受益，无需单独排期。
