# AI Quant Python - 量化交易系统 V1.0 实施方案

> 核心原则：**数据是基石，回测是验证，稳定第一，准确至上，效率其次**

---

## 一、技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| **主数据库** | PostgreSQL 16 + TimescaleDB | JSONB、分析函数、Grafana原生支持、时序自动分区压缩 |
| **回测引擎** | VectorBT (免费版) | 向量化极速、trade records提取完善、与Pandas深度集成 |
| **任务调度** | APScheduler 3.x + FastAPI | 轻量级、无需额外数据库、Python原生、可自定义Web管理界面 |
| **Web管理** | FastAPI | 异步高性能、自动API文档、与APScheduler无缝集成 |
| **ORM** | SQLAlchemy 2.0 | 批量操作成熟、模型定义清晰 |
| **可视化** | Grafana | PG原生数据源、Annotations标注交易点、模板变量 |

### 1.1 为什么不用 Airflow？

| 维度 | Airflow | APScheduler + FastAPI |
|------|---------|----------------------|
| 部署复杂度 | 需要独立服务+metadata数据库 | 嵌入Python进程，零额外依赖 |
| 学习成本 | DAG/Operator/XCom概念多 | 标准Python函数调度 |
| 资源占用 | 1GB+ 内存 | <100MB |
| 适用场景 | 复杂DAG编排、团队协作 | 个人/小团队量化系统 |
| Web UI | 通用但冗余 | 定制化，专注回测管理 |
| 定时能力 | ★★★★★ | ★★★★（满足需求） |

结论：本系统调度逻辑简单（定时同步→质检→回测），不需要Airflow的DAG编排能力。APScheduler + FastAPI更轻量、更可控。

### 1.2 PostgreSQL + TimescaleDB 性能

| 数据规模 | 行数 | PG+TimescaleDB | 结论 |
|---------|------|---------------|------|
| 日线 | ~1875万 | <10ms查询 | 够用 |
| 分钟线 | ~43亿 | 0.05-5s（压缩+连续聚合） | 必须TimescaleDB |
| 回测加载 | 500股×15年 | 1-3s | 可接受 |

---

## 二、系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                 FastAPI + APScheduler (单进程)               │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ 定时调度器    │  │ 回测管理API  │  │ Web管理界面       │  │
│  │ ─────────── │  │ ─────────── │  │ ─────────────── │  │
│  │ 每日18:00同步 │  │ POST /run   │  │ 同步状态监控     │  │
│  │ 同步后质检   │  │ GET /status │  │ 回测任务管理     │  │
│  │ 定期回测执行 │  │ GET /result │  │ 策略参数配置     │  │
│  └──────┬───────┘  └──────┬───────┘  └─────────┬─────────┘  │
└─────────┼─────────────────┼────────────────────┼────────────┘
          │                 │                    │
          ▼                 ▼                    ▼
┌──────────────────────────────────────────────────────────────┐
│                   PostgreSQL + TimescaleDB                   │
│                                                              │
│  ┌───────────────┐ ┌─────────────────┐ ┌────────────────┐  │
│  │ 原始数据(只读) │ │ 回测结果        │ │ 运维元数据     │  │
│  │ ─────────── │ │ ───────────── │ │ ──────────── │  │
│  │ stock_basic  │ │ backtest_runs  │ │ sync_log      │  │
│  │ daily        │ │ trade_records  │ │ sync_checkpoint│  │
│  │ daily_basic  │ │ equity_curves  │ │ data_quality   │  │
│  │ adj_factor   │ │ position_snap  │ │                │  │
│  │ fund_basic   │ │                │ │                │  │
│  │ fund_daily   │ │                │ │                │  │
│  │ trade_cal    │ │                │ │                │  │
│  └───────────────┘ └─────────────────┘ └────────────────┘  │
│                                                              │
│  TimescaleDB Hypertables: daily, fund_daily, adj_factor,     │
│  daily_basic (后期: 分钟线)                                   │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │      Grafana        │
                  │ 数据监控 | 回测分析  │
                  └─────────────────────┘
```

### 2.1 核心设计原则

**稳定性：**
- 同步任务按日提交（一天失败不影响其他天）
- 断点续传（sync_checkpoint 记录每表最后同步日期）
- 失败重试（指数退避，最多3次）
- 同步日志完整记录（sync_log 追踪每次操作）

**准确性：**
- 数据表字段与tushare API原始返回完全一致，不做合并/裁剪
- 回测引擎严格模拟A股交易规则（T+1、涨跌停、整手交易）
- 每笔交易记录完整保存，可逐笔审计
- 前视偏差（look-ahead bias）防护

**高效性：**
- VectorBT向量化回测，500股×5年 < 30秒
- PostgreSQL批量写入（COPY协议）
- TimescaleDB自动分区+压缩，查询性能随数据量线性扩展

---

## 三、项目目录结构

```
ai_quant_python/
├── .env                              # 环境变量
├── docker-compose.yml                # PG+TimescaleDB + Grafana
├── requirements.txt
│
├── config/                           # 配置层
│   ├── __init__.py
│   ├── settings.py                   # 全局配置(DB/Token/路径)
│   └── data_sync_config.py           # ★ 数据同步任务配置(统一入口)
│
├── data/                             # 数据层
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py             # SQLAlchemy引擎+连接池
│   │   ├── models.py                 # ★ ORM模型(原始数据+回测结果+运维元数据)
│   │   ├── base_repo.py              # 基础Repository(通用CRUD+批量写入)
│   │   └── timescale.py              # hypertable创建/压缩/连续聚合
│   │
│   ├── sync/
│   │   ├── __init__.py
│   │   ├── engine.py                 # ★ 同步引擎(读config→调度执行→写日志)
│   │   ├── tushare_client.py         # Tushare API封装(重试/限流/断点)
│   │   └── scheduler.py              # APScheduler定时任务配置
│   │
│   └── quality/
│       ├── __init__.py
│       ├── checker.py                # 质检调度器
│       ├── rules.py                  # ★ 检验规则定义(含具体阈值)
│       └── reporter.py               # 质检报告(写DB+告警)
│
├── backtest/                         # 回测系统
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── vbt_engine.py             # VectorBT引擎封装
│   │   ├── data_loader.py            # ★ 从PG加载+拼接回测数据
│   │   └── result_extractor.py       # 结果提取(trades/equity/stats)
│   │
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── base.py                   # 策略基类(标准化接口)
│   │   ├── registry.py               # 策略注册中心(插件式发现)
│   │   ├── ma_cross.py               # 均线交叉
│   │   └── momentum.py               # 动量策略
│   │
│   ├── broker/
│   │   ├── __init__.py
│   │   └── a_share.py                # ★ A股交易规则(T+1/涨跌停/整手/佣金)
│   │
│   ├── performance/
│   │   ├── __init__.py
│   │   └── metrics.py                # 绩效指标(Sharpe/Sortino/Calmar等)
│   │
│   └── storage/
│       ├── __init__.py
│       ├── persistor.py              # ★ 回测结果写入PG
│       └── queries.py                # Grafana专用查询SQL
│
├── web/                              # Web管理界面
│   ├── __init__.py
│   ├── app.py                        # FastAPI应用入口
│   ├── api/
│   │   ├── __init__.py
│   │   ├── sync_api.py               # 同步管理API
│   │   ├── backtest_api.py           # 回测任务API
│   │   └── quality_api.py            # 质检查询API
│   └── static/                       # 前端静态文件(简易管理页)
│
├── visualization/grafana/            # Grafana配置
│   ├── provisioning/
│   │   ├── datasources.yaml
│   │   └── dashboards.yaml
│   └── dashboards/
│       ├── backtest_overview.json
│       ├── trade_analysis.json
│       └── data_monitor.json
│
├── utils/
│   ├── __init__.py
│   ├── logger.py                     # 结构化日志
│   └── trade_calendar.py             # 交易日历工具
│
├── scripts/
│   ├── init_db.py                    # 初始化数据库
│   └── run_sync.py                   # CLI手动同步
│
└── tests/
    ├── test_sync.py
    ├── test_backtest.py
    └── test_quality.py
```

---

## 四、数据库Schema设计

### 4.1 原始数据表（与tushare API字段一一对应）

```sql
-- ★ 核心原则：字段与tushare API返回完全一致，不合并、不裁剪

-- 交易日历（同步前置依赖）
CREATE TABLE trade_cal (
    exchange        VARCHAR(10) NOT NULL,
    cal_date        DATE NOT NULL,
    is_open         INTEGER,
    pretrade_date   DATE,
    PRIMARY KEY (exchange, cal_date)
);

-- 股票基本信息
CREATE TABLE stock_basic (
    ts_code         VARCHAR(20) PRIMARY KEY,
    symbol          VARCHAR(10),
    name            VARCHAR(50),
    area            VARCHAR(20),
    industry        VARCHAR(50),
    fullname        VARCHAR(100),
    market          VARCHAR(10),
    exchange        VARCHAR(10),
    curr_type       VARCHAR(10),
    list_status     VARCHAR(10),
    list_date       DATE,
    delist_date     DATE,
    is_hs           VARCHAR(5)
);

-- 日线行情（tushare daily接口原始字段）
CREATE TABLE daily (
    ts_code         VARCHAR(20) NOT NULL,
    trade_date      DATE NOT NULL,
    open            DOUBLE PRECISION,
    high            DOUBLE PRECISION,
    low             DOUBLE PRECISION,
    close           DOUBLE PRECISION,
    pre_close       DOUBLE PRECISION,
    change          DOUBLE PRECISION,
    pct_chg         DOUBLE PRECISION,
    vol             DOUBLE PRECISION,
    amount          DOUBLE PRECISION,
    PRIMARY KEY (ts_code, trade_date)
);
SELECT create_hypertable('daily', 'trade_date', chunk_time_interval => INTERVAL '7 days');

-- 每日指标（tushare daily_basic接口原始字段）
CREATE TABLE daily_basic (
    ts_code         VARCHAR(20) NOT NULL,
    trade_date      DATE NOT NULL,
    close           DOUBLE PRECISION,
    turnover_rate   DOUBLE PRECISION,
    turnover_rate_f DOUBLE PRECISION,
    volume_ratio    DOUBLE PRECISION,
    pe              DOUBLE PRECISION,
    pe_ttm          DOUBLE PRECISION,
    pb              DOUBLE PRECISION,
    ps              DOUBLE PRECISION,
    ps_ttm          DOUBLE PRECISION,
    dv_ratio        DOUBLE PRECISION,
    dv_ttm          DOUBLE PRECISION,
    total_share     DOUBLE PRECISION,
    float_share     DOUBLE PRECISION,
    free_share      DOUBLE PRECISION,
    total_mv        DOUBLE PRECISION,
    circ_mv         DOUBLE PRECISION,
    PRIMARY KEY (ts_code, trade_date)
);
SELECT create_hypertable('daily_basic', 'trade_date', chunk_time_interval => INTERVAL '7 days');

-- 复权因子（tushare adj_factor接口原始字段）
CREATE TABLE adj_factor (
    ts_code         VARCHAR(20) NOT NULL,
    trade_date      DATE NOT NULL,
    adj_factor      DOUBLE PRECISION,
    PRIMARY KEY (ts_code, trade_date)
);
SELECT create_hypertable('adj_factor', 'trade_date', chunk_time_interval => INTERVAL '7 days');

-- 基金基本信息
CREATE TABLE fund_basic (
    ts_code         VARCHAR(20) PRIMARY KEY,
    name            VARCHAR(50),
    management      VARCHAR(50),
    custodian       VARCHAR(50),
    fund_type       VARCHAR(20),
    found_date      DATE,
    due_date        DATE,
    list_date       DATE,
    issue_date      DATE,
    delist_date     DATE,
    issue_amount    DOUBLE PRECISION,
    m_fee           DOUBLE PRECISION,
    c_fee           DOUBLE PRECISION,
    duration_year   DOUBLE PRECISION,
    p_value         DOUBLE PRECISION,
    min_amount      DOUBLE PRECISION,
    exp_return      DOUBLE PRECISION,
    benchmark       TEXT,
    status          VARCHAR(10),
    invest_type     VARCHAR(20),
    "type"          VARCHAR(20),
    trustee         VARCHAR(50),
    purc_startdate  DATE,
    redm_startdate  DATE,
    market          VARCHAR(5)
);

-- 基金日线行情
CREATE TABLE fund_daily (
    ts_code         VARCHAR(20) NOT NULL,
    trade_date      DATE NOT NULL,
    open            DOUBLE PRECISION,
    high            DOUBLE PRECISION,
    low             DOUBLE PRECISION,
    close           DOUBLE PRECISION,
    pre_close       DOUBLE PRECISION,
    change          DOUBLE PRECISION,
    pct_chg         DOUBLE PRECISION,
    vol             DOUBLE PRECISION,
    amount          DOUBLE PRECISION,
    PRIMARY KEY (ts_code, trade_date)
);
SELECT create_hypertable('fund_daily', 'trade_date', chunk_time_interval => INTERVAL '30 days');
```

### 4.2 回测结果表

```sql
-- 回测主表
CREATE TABLE backtest_runs (
    id              SERIAL PRIMARY KEY,
    strategy_name   VARCHAR(100) NOT NULL,
    strategy_params JSONB NOT NULL DEFAULT '{}',
    symbols         JSONB NOT NULL DEFAULT '[]',
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    init_cash       DOUBLE PRECISION DEFAULT 100000.0,
    commission_rate DOUBLE PRECISION DEFAULT 0.00025,
    slippage_rate   DOUBLE PRECISION DEFAULT 0.001,
    status          VARCHAR(20) DEFAULT 'pending',
    total_return    DOUBLE PRECISION,
    annual_return   DOUBLE PRECISION,
    max_drawdown    DOUBLE PRECISION,
    sharpe_ratio    DOUBLE PRECISION,
    sortino_ratio   DOUBLE PRECISION,
    calmar_ratio    DOUBLE PRECISION,
    win_rate        DOUBLE PRECISION,
    profit_factor   DOUBLE PRECISION,
    total_trades    INTEGER,
    final_value     DOUBLE PRECISION,
    result_json     JSONB,
    error_message   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX idx_br_strategy ON backtest_runs(strategy_name);
CREATE INDEX idx_br_status ON backtest_runs(status);
CREATE INDEX idx_br_created ON backtest_runs(created_at DESC);
CREATE INDEX idx_br_config ON backtest_runs USING GIN (strategy_params);

-- 交易记录（每一笔交易完整记录）
CREATE TABLE trade_records (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER REFERENCES backtest_runs(id) ON DELETE CASCADE,
    trade_idx       INTEGER,
    symbol          VARCHAR(20) NOT NULL,
    direction       VARCHAR(10) NOT NULL,
    entry_time      TIMESTAMPTZ,
    exit_time       TIMESTAMPTZ,
    entry_price     DOUBLE PRECISION NOT NULL,
    exit_price      DOUBLE PRECISION,
    size            DOUBLE PRECISION NOT NULL,
    pnl             DOUBLE PRECISION DEFAULT 0,
    return_pct      DOUBLE PRECISION DEFAULT 0,
    fees            DOUBLE PRECISION DEFAULT 0,
    duration_bars   INTEGER,
    status          VARCHAR(20) DEFAULT 'closed'
);
CREATE INDEX idx_tr_run ON trade_records(run_id);
CREATE INDEX idx_tr_symbol ON trade_records(symbol);
CREATE INDEX idx_tr_entry ON trade_records(entry_time);

-- 净值曲线（Grafana绘图核心数据源，不做hypertable，数据量小）
CREATE TABLE equity_curves (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER REFERENCES backtest_runs(id) ON DELETE CASCADE,
    timestamp       TIMESTAMPTZ NOT NULL,
    equity_value    DOUBLE PRECISION NOT NULL,
    drawdown        DOUBLE PRECISION DEFAULT 0,
    daily_return    DOUBLE PRECISION DEFAULT 0,
    cash_value      DOUBLE PRECISION DEFAULT 0,
    positions_value DOUBLE PRECISION DEFAULT 0
);
CREATE INDEX idx_ec_run_ts ON equity_curves(run_id, timestamp);

-- 持仓快照
CREATE TABLE position_snapshots (
    id              SERIAL PRIMARY KEY,
    run_id          INTEGER REFERENCES backtest_runs(id) ON DELETE CASCADE,
    timestamp       TIMESTAMPTZ NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    quantity        DOUBLE PRECISION NOT NULL,
    avg_cost        DOUBLE PRECISION NOT NULL,
    market_value    DOUBLE PRECISION NOT NULL,
    weight          DOUBLE PRECISION,
    unrealized_pnl  DOUBLE PRECISION DEFAULT 0
);
CREATE INDEX idx_ps_run_ts ON position_snapshots(run_id, timestamp);
```

### 4.3 运维元数据表

```sql
-- 同步日志（每次同步操作的完整记录）
CREATE TABLE sync_log (
    id              SERIAL PRIMARY KEY,
    table_name      VARCHAR(100) NOT NULL,
    sync_mode       VARCHAR(20) NOT NULL,       -- incremental/full/once
    start_time      TIMESTAMPTZ NOT NULL,
    end_time        TIMESTAMPTZ,
    status          VARCHAR(20) NOT NULL,        -- running/completed/failed
    rows_fetched    INTEGER DEFAULT 0,
    rows_inserted   INTEGER DEFAULT 0,
    error_message   TEXT,
    details         JSONB DEFAULT '{}'           -- 额外信息(如具体日期范围)
);
CREATE INDEX idx_sl_table_time ON sync_log(table_name, start_time DESC);

-- 同步检查点（每表最后同步日期，用于增量续传）
CREATE TABLE sync_checkpoint (
    table_name      VARCHAR(100) PRIMARY KEY,
    last_sync_date  DATE NOT NULL,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 数据质量日志
CREATE TABLE data_quality_log (
    id              SERIAL PRIMARY KEY,
    table_name      VARCHAR(100) NOT NULL,
    check_date      DATE NOT NULL,
    rule_name       VARCHAR(100) NOT NULL,
    status          VARCHAR(20) NOT NULL,         -- pass/warn/fail
    total_rows      INTEGER,
    issue_count     INTEGER,
    details         JSONB DEFAULT '[]',
    checked_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_dql_table_date ON data_quality_log(table_name, check_date DESC);
```

---

## 五、核心模块设计

### 5.1 数据同步配置 (config/data_sync_config.py)

```python
DATA_SYNC_TASKS = {
    # ===== 前置依赖 =====
    "trade_cal": {
        "name": "交易日历",
        "api": "trade_cal",
        "mode": "once",
        "schedule": "0 6 * * *",              # 每天6点全量刷新
        "priority": 0,                         # 最高优先级，其他表依赖此表
        "params": {"exchange": ""},
        "fields": {
            'exchange': ('str', '交易所', True),
            'cal_date': ('date', '日历日期', True),
            'is_open': ('int', '是否交易', False),
            'pretrade_date': ('date', '上一交易日', False),
        },
        "is_timescale": False,
    },

    # ===== 股票数据 =====
    "stock_basic": {
        "name": "股票基本信息",
        "api": "stock_basic",
        "mode": "once",
        "schedule": "0 7 * * *",
        "priority": 1,
        "params": {"exchange": "", "list_status": "L"},
        "fields": {
            'ts_code': ('str', 'TS代码', True),
            'symbol': ('str', '代码', False),
            'name': ('str', '名称', False),
            'area': ('str', '地域', False),
            'industry': ('str', '行业', False),
            'fullname': ('str', '全称', False),
            'market': ('str', '市场', False),
            'exchange': ('str', '交易所', False),
            'curr_type': ('str', '货币', False),
            'list_status': ('str', '状态', False),
            'list_date': ('date', '上市日', False),
            'delist_date': ('date', '退市日', False),
            'is_hs': ('str', '沪深港通', False),
        },
        "is_timescale": False,
    },

    "daily": {
        "name": "日线行情",
        "api": "daily",
        "mode": "incremental",
        "date_field": "trade_date",
        "schedule": "0 18 * * 1-5",
        "priority": 2,
        "oldest_date": "20100101",
        "api_date_type": "single",             # 按trade_date遍历
        "fields": {
            'ts_code': ('str', '代码', True),
            'trade_date': ('date', '日期', True),
            'open': ('float', '开盘', False),
            'high': ('float', '最高', False),
            'low': ('float', '最低', False),
            'close': ('float', '收盘', False),
            'pre_close': ('float', '昨收', False),
            'change': ('float', '涨跌额', False),
            'pct_chg': ('float', '涨跌幅', False),
            'vol': ('float', '成交量', False),
            'amount': ('float', '成交额', False),
        },
        "is_timescale": True,
        "chunk_interval": "7 days",
        "compress_after": "90 days",
        "quality_rules": ["no_null_price", "positive_volume", "reasonable_change",
                          "price_consistency", "no_missing_dates"],
    },

    "daily_basic": {
        "name": "每日指标",
        "api": "daily_basic",
        "mode": "incremental",
        "date_field": "trade_date",
        "schedule": "0 18 * * 1-5",
        "priority": 2,
        "oldest_date": "20100101",
        "api_date_type": "single",
        "fields": {
            'ts_code': ('str', '代码', True),
            'trade_date': ('date', '日期', True),
            'close': ('float', '收盘价', False),
            'turnover_rate': ('float', '换手率', False),
            'turnover_rate_f': ('float', '换手率(自由)', False),
            'volume_ratio': ('float', '量比', False),
            'pe': ('float', 'PE', False),
            'pe_ttm': ('float', 'PE-TTM', False),
            'pb': ('float', 'PB', False),
            'ps': ('float', 'PS', False),
            'ps_ttm': ('float', 'PS-TTM', False),
            'dv_ratio': ('float', '股息率', False),
            'dv_ttm': ('float', '股息率TTM', False),
            'total_share': ('float', '总股本', False),
            'float_share': ('float', '流通股本', False),
            'free_share': ('float', '自由流通', False),
            'total_mv': ('float', '总市值', False),
            'circ_mv': ('float', '流通市值', False),
        },
        "is_timescale": True,
        "chunk_interval": "7 days",
        "compress_after": "90 days",
        "quality_rules": ["no_null_price", "reasonable_pe"],
    },

    "adj_factor": {
        "name": "复权因子",
        "api": "adj_factor",
        "mode": "incremental",
        "date_field": "trade_date",
        "schedule": "0 18 * * 1-5",
        "priority": 2,
        "oldest_date": "20100101",
        "api_date_type": "single",
        "fields": {
            'ts_code': ('str', '代码', True),
            'trade_date': ('date', '日期', True),
            'adj_factor': ('float', '复权因子', False),
        },
        "is_timescale": True,
        "chunk_interval": "7 days",
        "compress_after": "90 days",
        "quality_rules": ["adj_factor_positive"],
    },

    # ===== 基金数据 =====
    "fund_basic": {
        "name": "基金基本信息",
        "api": "fund_basic",
        "mode": "once",
        "schedule": "0 7 * * *",
        "priority": 3,
        "fields": { /* 同tushare fund_basic接口 */ },
        "is_timescale": False,
    },

    "fund_daily": {
        "name": "基金日线行情",
        "api": "fund_daily",
        "mode": "incremental",
        "date_field": "trade_date",
        "schedule": "0 19 * * 1-5",
        "priority": 4,
        "oldest_date": "20100101",
        "api_date_type": "single",
        "fields": { /* 同tushare fund_daily接口 */ },
        "is_timescale": True,
        "chunk_interval": "30 days",
        "quality_rules": ["no_null_price", "positive_volume"],
    },

    # ===== 后期扩展 =====
    "stk_mins_1min": {
        "name": "1分钟线",
        "api": "stk_mins",
        "mode": "incremental",
        "enabled": False,
        "params": {"freq": "1min"},
        "is_timescale": True,
        "chunk_interval": "1 day",
        "compress_after": "7 days",
    },
}
```

### 5.2 同步引擎 (data/sync/engine.py)

```python
class SyncEngine:
    """
    配置驱动的通用同步引擎。

    稳定性设计:
    - 每个交易日独立事务，失败不影响其他日
    - sync_checkpoint记录断点，支持续传
    - sync_log完整记录每次操作
    - UPSERT语义，重复同步不产生脏数据
    """

    def sync(self, table_name: str = None, mode: str = None):
        """
        用法:
          engine.sync()                    # 同步所有已启用表
          engine.sync("daily")             # 只同步daily
          engine.sync("daily", mode="full") # daily强制全量
        """

    def _sync_once(self, cfg):
        """全量一次性拉取（如stock_basic）"""

    def _sync_incremental(self, cfg):
        """
        增量同步核心逻辑:
        1. 查sync_checkpoint获取last_sync_date
        2. 查trade_cal获取待同步交易日列表
        3. 逐日调用tushare API
        4. 每日数据独立事务写入PG (INSERT ON CONFLICT DO UPDATE)
        5. 更新sync_checkpoint
        6. 写sync_log
        """

    def _sync_full(self, cfg):
        """全量重建: TRUNCATE → 重新拉取 → 更新checkpoint"""
```

### 5.3 质检规则 (data/quality/rules.py)

```python
QUALITY_RULES = {
    "no_null_price": {
        "name": "价格非空检查",
        "check_cols": ["open", "high", "low", "close"],
        "threshold": 0.0,               # null率>0% → fail
        "level": "fail",
    },
    "positive_volume": {
        "name": "成交量非负检查",
        "check_cols": ["vol"],
        "condition": ">= 0",
        "level": "fail",
    },
    "reasonable_change": {
        "name": "涨跌幅合理性",
        "check_cols": ["pct_chg"],
        "threshold": 30.0,              # |pct_chg|>30% → warn
        "level": "warn",
    },
    "price_consistency": {
        "name": "价格逻辑一致性",
        "rules": [
            "high >= low",
            "high >= open",
            "high >= close",
            "low <= open",
            "low <= close",
        ],
        "level": "fail",
    },
    "no_missing_dates": {
        "name": "交易日完整性",
        "compare_with": "trade_cal",     # 对比交易日历
        "level": "warn",
    },
    "adj_factor_positive": {
        "name": "复权因子正数",
        "check_cols": ["adj_factor"],
        "condition": "> 0",
        "level": "fail",
    },
    "reasonable_pe": {
        "name": "PE合理性",
        "check_cols": ["pe_ttm"],
        "threshold": 10000,              # PE>10000 → warn
        "level": "warn",
    },
}
```

### 5.4 A股交易规则 (backtest/broker/a_share.py)

```python
class AShareBroker:
    """
    A股交易规则模拟器，确保回测结果真实可靠。

    规则1: T+1 — 当日买入次日才能卖出
    规则2: 涨跌停 — 主板±10%，科创板/创业板±20%，ST±5%
    规则3: 整手交易 — 买入必须100股整数倍，卖出可零卖
    规则4: 佣金 — 券商佣金万2.5，最低5元
    规则5: 印花税 — 卖出0.05%（2023-08-28起）
    规则6: 过户费 — 0.001%（双向）
    """

    COMMISSION_RATE = 0.00025           # 佣金率万2.5
    COMMISSION_MIN = 5.0                # 最低佣金5元
    STAMP_DUTY_RATE = 0.0005            # 印花税0.05%(卖出)
    TRANSFER_FEE_RATE = 0.00001         # 过户费0.001%
    LOT_SIZE = 100                      # 整手数量

    MAIN_BOARD_LIMIT = 0.10             # 主板涨跌停10%
    STAR_BOARD_LIMIT = 0.20             # 科创板涨跌停20%
    ST_LIMIT = 0.05                     # ST涨跌停5%

    @staticmethod
    def calc_commission(amount: float) -> float:
        """计算佣金（最低5元）"""
        return max(amount * AShareBroker.COMMISSION_RATE, AShareBroker.COMMISSION_MIN)

    @staticmethod
    def calc_stamp_duty(amount: float) -> float:
        """计算印花税（仅卖出）"""
        return amount * AShareBroker.STAMP_DUTY_RATE

    @staticmethod
    def calc_total_fees(buy_amount: float, sell_amount: float) -> float:
        """计算一笔完整交易的总费用"""
        buy_commission = AShareBroker.calc_commission(buy_amount)
        sell_commission = AShareBroker.calc_commission(sell_amount)
        stamp_duty = AShareBroker.calc_stamp_duty(sell_amount)
        transfer_fee = (buy_amount + sell_amount) * AShareBroker.TRANSFER_FEE_RATE
        return buy_commission + sell_commission + stamp_duty + transfer_fee

    @staticmethod
    def round_to_lot(shares: int) -> int:
        """向下取整到整手"""
        return (shares // AShareBroker.LOT_SIZE) * AShareBroker.LOT_SIZE
```

### 5.5 回测数据加载器 (backtest/engine/data_loader.py)

```python
class DataLoader:
    """
    从PG加载回测数据，自动拼接 daily + daily_basic + adj_factor。
    
    关键设计:
    - 使用前复权价格（close * adj_factor / latest_adj_factor）
    - 不使用未来数据（adj_factor取回测区间内的值）
    - 返回VectorBT所需的宽格式DataFrame
    """

    def load(self, symbols: list, start: str, end: str) -> dict:
        """
        加载回测数据。
        
        Returns:
            {
                'open': DataFrame(symbols×dates),
                'high': DataFrame,
                'low': DataFrame,
                'close': DataFrame,      # 前复权收盘价
                'volume': DataFrame,
                'adj_factor': DataFrame,  # 原始复权因子
                'pe_ttm': DataFrame,     # 基本面数据
                ...
            }
        """
        sql = """
        SELECT d.ts_code, d.trade_date,
               d.open, d.high, d.low, d.close, d.vol,
               db.pe_ttm, db.pb, db.turnover_rate, db.total_mv,
               af.adj_factor
        FROM daily d
        LEFT JOIN daily_basic db ON d.ts_code = db.ts_code AND d.trade_date = db.trade_date
        LEFT JOIN adj_factor af ON d.ts_code = af.ts_code AND d.trade_date = af.trade_date
        WHERE d.ts_code = ANY(:symbols)
          AND d.trade_date BETWEEN :start AND :end
        ORDER BY d.ts_code, d.trade_date
        """
```

### 5.6 策略基类 (backtest/strategies/base.py)

```python
class StrategyBase:
    """
    策略基类。

    前视偏差防护:
    - generate_signals只能使用trade_date之前的数据
    - VectorBT的from_signals天然避免了前视偏差（信号在下一bar执行）
    """

    name: str = "base"
    description: str = ""

    def generate_signals(self, data: dict, config: dict) -> tuple:
        """
        生成交易信号。

        Args:
            data: DataLoader.load()返回的字典，含open/high/low/close/volume等
            config: 策略参数

        Returns:
            (entries, exits): 两个bool DataFrame, columns=symbols, index=dates
        """
        raise NotImplementedError

    def get_default_config(self) -> dict:
        return {}
```

### 5.7 Web管理API (web/app.py)

```python
# FastAPI应用入口，集成APScheduler

app = FastAPI(title="AI Quant Python", version="1.0")

# API端点:
# POST /api/sync/{table_name}          - 触发单表同步
# POST /api/sync/all                   - 触发全量同步
# GET  /api/sync/status                - 查看同步状态
# GET  /api/sync/log                   - 查看同步日志
# POST /api/backtest/run               - 提交回测任务
# GET  /api/backtest/status/{run_id}   - 查询回测状态
# GET  /api/backtest/result/{run_id}   - 获取回测结果
# GET  /api/backtest/trades/{run_id}   - 获取交易记录
# GET  /api/quality/check/{table}      - 执行质检
# GET  /api/quality/log                - 查看质检日志
# GET  /api/strategies                 - 列出可用策略
```

### 5.8 Docker Compose

```yaml
version: '3.8'
services:
  postgres:
    image: timescale/timescaledb:latest-pg16
    environment:
      POSTGRES_DB: quant_db
      POSTGRES_USER: quant
      POSTGRES_PASSWORD: ${DB_PASSWORD:-quant123}
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U quant -d quant_db"]
      interval: 10s
      timeout: 5s
      retries: 5

  grafana:
    image: grafana/grafana:11.0.0
    depends_on:
      postgres:
        condition: service_healthy
    ports: ["3000:3000"]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-admin}
    volumes:
      - ./visualization/grafana/provisioning:/etc/grafana/provisioning
      - ./visualization/grafana/dashboards:/var/lib/grafana/dashboards
      - grafanadata:/var/lib/grafana

volumes:
  pgdata:
  grafanadata:
```

注意：Python应用（FastAPI + APScheduler）直接在本地运行，不放入Docker，便于开发和调试。

---

## 六、实施计划

### Phase 1: 项目基础设施 (Day 1-2)

| 任务 | 产出 | 时间 |
|------|------|------|
| 项目骨架 + 目录 + requirements.txt | 完整目录树 | 0.5h |
| docker-compose.yml | PG+Grafana一键启动 | 0.5h |
| config/settings.py | 全局配置 | 0.5h |
| utils/logger.py | 结构化日志 | 0.5h |
| data/database/connection.py | SQLAlchemy引擎 | 0.5h |
| data/database/models.py | 所有ORM模型 | 2h |
| data/database/base_repo.py | 通用CRUD+批量写入 | 1h |
| data/database/timescale.py | hypertable工具 | 0.5h |
| scripts/init_db.py | 数据库初始化脚本 | 0.5h |
| 验证 | docker compose up → PG+Grafana正常 | 0.5h |

### Phase 2: 数据同步系统 (Day 3-5)

| 任务 | 产出 | 时间 |
|------|------|------|
| config/data_sync_config.py | 完整任务配置(7个表) | 1.5h |
| data/sync/tushare_client.py | API封装(重试/限流) | 1.5h |
| data/sync/engine.py | 同步引擎(once/incremental/full) | 3h |
| data/sync/scheduler.py | APScheduler定时配置 | 1h |
| scripts/run_sync.py | CLI手动同步 | 0.5h |
| 端到端测试 | trade_cal→stock_basic→daily→daily_basic→adj_factor→fund_basic→fund_daily | 2h |
| 验证 | 增量同步断点续传、重复同步幂等 | 1h |

### Phase 3: 质检模块 (Day 6)

| 任务 | 产出 | 时间 |
|------|------|------|
| data/quality/rules.py | 7条规则定义(含阈值) | 1.5h |
| data/quality/checker.py | 质检调度器 | 1.5h |
| data/quality/reporter.py | 报告生成 | 1h |
| 同步后自动质检 | engine→checker联动 | 1h |
| 验证 | 数据异常能被检出 | 1h |

### Phase 4: 回测系统 (Day 7-10)

| 任务 | 产出 | 时间 |
|------|------|------|
| backtest/strategies/base.py | 策略基类 | 1h |
| backtest/strategies/registry.py | 注册中心 | 0.5h |
| backtest/strategies/ma_cross.py | 均线交叉策略 | 1h |
| backtest/strategies/momentum.py | 动量策略 | 1h |
| backtest/broker/a_share.py | A股交易规则 | 1.5h |
| backtest/engine/data_loader.py | PG数据加载+拼接 | 2h |
| backtest/engine/vbt_engine.py | VectorBT封装 | 2.5h |
| backtest/engine/result_extractor.py | 结果提取 | 1.5h |
| backtest/storage/persistor.py | 结果写入PG | 2h |
| backtest/performance/metrics.py | 绩效指标 | 1h |
| 端到端测试 | ma_cross回测→结果入PG | 1.5h |
| 验证 | 交易记录可查、净值曲线正确、费用计算准确 | 1h |

### Phase 5: Web + Grafana (Day 11-12)

| 任务 | 产出 | 时间 |
|------|------|------|
| web/app.py | FastAPI应用 | 1h |
| web/api/sync_api.py | 同步管理API | 1h |
| web/api/backtest_api.py | 回测任务API | 1.5h |
| web/api/quality_api.py | 质检查询API | 0.5h |
| web/static/ | 简易管理页面 | 2h |
| Grafana datasources | PG数据源配置 | 0.5h |
| Grafana backtest_overview.json | 回测概览Dashboard | 2h |
| Grafana trade_analysis.json | 交易分析Dashboard | 1.5h |
| Grafana data_monitor.json | 数据监控Dashboard | 1h |
| 验证 | 全链路可用 | 1h |

---

## 七、技术风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| Tushare API限流 | 高 | 中 | 智能限速+错峰同步+失败队列 |
| 增量同步中断 | 中 | 高 | sync_checkpoint断点续传 |
| 数据修正覆盖 | 低 | 高 | UPSERT语义，支持指定日期范围重同步 |
| 回测前视偏差 | 中 | 高 | VectorBT from_signals天然防护+策略基类约束 |
| VectorBT内存溢出 | 低 | 高 | 分批回测+内存监控 |
| PG分钟线查询慢 | 后期 | 高 | TimescaleDB压缩+连续聚合+Parquet缓存 |

---

## 八、后期扩展预留

### 8.1 AI模型接入
- StrategyBase 预留 `predict()` 方法
- 特征工程模块独立于策略，可复用
- 模型信号可与传统规则信号组合

### 8.2 分钟线数据
- 配置已预留 `stk_mins_1min`，`enabled: False`
- TimescaleDB 1天分区+7天压缩策略已设计

### 8.3 交易系统
- commission/slippage可直接复用
- trade_records表结构与实盘格式一致
