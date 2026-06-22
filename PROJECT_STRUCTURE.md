# AI Quant Python - 项目代码结构说明

## 项目概述

这是一个 **中国A股量化交易系统**，包含数据同步、回测引擎、Web管理界面和Grafana可视化四大模块。

**核心功能：**
- 从 Tushare API 同步A股全量历史数据（1991年至今）到 PostgreSQL + TimescaleDB
- 基于 VectorBT 的多策略回测（MACD、均线交叉、动量策略）
- FastAPI 管理后台，支持手动触发同步/回测/数据质量检查
- Grafana 仪表盘展示K线图（含不复权/前复权/后复权）、成交量、财务指标等

---

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 数据源 | Tushare Pro API | A股行情、财务、资金流向数据 |
| 数据库 | PostgreSQL 16 + TimescaleDB | 时序数据存储，支持超表和压缩 |
| 回测引擎 | VectorBT (免费版) | 向量化回测框架 |
| Web框架 | FastAPI + Uvicorn | REST API + 管理后台 |
| 可视化 | Grafana 13.0 | 仪表盘展示 |
| 调度 | APScheduler | 定时任务调度 |

---

## 目录结构总览

```
ai_quant_python/
├── config/                  # 配置模块
├── data/                    # 数据层（同步、存储、质量检查）
│   ├── database/            # 数据库连接、ORM模型、TimescaleDB
│   ├── sync/                # 数据同步引擎
│   └── quality/             # 数据质量检查
├── backtest/                # 回测引擎
│   ├── broker/              # A股券商模拟
│   ├── engine/              # 回测核心逻辑
│   ├── strategies/          # 策略实现
│   ├── performance/         # 绩效指标计算
│   └── storage/             # 回测结果持久化
├── web/                     # Web应用（FastAPI）
│   ├── api/                 # API路由
│   └── static/              # 前端页面
├── visualization/           # Grafana仪表盘配置
├── scripts/                 # 运维/工具脚本
├── utils/                   # 通用工具
├── logs/                    # 日志文件
└── tmp_*.py                 # 开发调试用的一次性脚本
```

---

## 详细文件说明

### 1. 项目根目录文件

| 文件 | 用途 |
|------|------|
| `PLAN.md` | 系统架构设计文档（V1.0），包含技术选型、数据库schema、API设计、部署方案 |
| `requirements.txt` | Python依赖：SQLAlchemy, psycopg2, pandas, numpy, tushare, vectorbt, FastAPI等 |
| `docker-compose.yml` | Docker Compose配置：TimescaleDB(PG16) + Grafana服务 |
| `run_backtest_task.py` | CLI入口：运行多策略回测并打印结果 |
| `grafana_queries.sql` | Grafana仪表盘的SQL查询模板 |
| `backtest_report.html` | 回测结果HTML报告（Chart.js可视化） |

### 2. `config/` - 配置模块

| 文件 | 用途 | 关联 |
|------|------|------|
| `settings.py` | 全局配置类，从 `.env` 加载数据库连接、Tushare token、日志目录 | 被所有模块引用 |
| `data_sync_config.py` | **核心配置**（407行）：定义13张表的同步任务，包含API名称、字段映射、调度计划、质量规则 | 被 `sync/engine.py`、`scripts/` 使用 |

**`data_sync_config.py` 结构：**
```python
DATA_SYNC_TASKS = {
    "trade_cal":     # 交易日历，mode=once，priority=0
    "stock_basic":   # 股票基本信息，mode=once，priority=1
    "daily":         # 日线行情，mode=incremental，priority=2
    "daily_basic":   # 每日指标(PE/PB/换手率)，mode=incremental
    "adj_factor":    # 复权因子，mode=incremental
    "fund_basic":    # 基金基本信息，mode=once
    "fund_daily":    # 基金日线，mode=incremental
    "moneyflow":     # 资金流向，mode=incremental，api_date_type="code"
    "income":        # 利润表，mode=incremental，api_date_type="code"
    "balancesheet":  # 资产负债表，mode=incremental，api_date_type="code"
    "cashflow":      # 现金流量表，mode=incremental，api_date_type="code"
    "fina_indicator": # 财务指标，mode=incremental，api_date_type="code"
    "fina_audit":    # 财务审计，mode=incremental，api_date_type="code"
}
```

**每个任务的字段定义格式：**
```python
"字段名": ("类型", "中文描述", 是否主键)
# 例如：
"ts_code": ("str", "代码", True),      # 主键
"trade_date": ("date", "日期", True),  # 主键
"open": ("float", "开盘", False),      # 非主键
```

### 3. `data/database/` - 数据库层

| 文件 | 用途 | 关联 |
|------|------|------|
| `connection.py` | SQLAlchemy引擎 + Session工厂，从 `settings.py` 读取连接参数 | 被所有数据层模块使用 |
| `models.py` | ORM模型定义（275行），定义13张表的结构 | 被 `sync/engine.py`、`backtest/storage/` 使用 |
| `timescale.py` | TimescaleDB超表创建、压缩策略、连续聚合 | 被 `scripts/init_db.py` 调用 |
| `base_repo.py` | 基础仓储类，提供 `bulk_upsert_df()` 批量插入 | 被回测结果存储使用 |

**`models.py` 中的13张表：**

```
市场数据表：
├── TradeCal          # 交易日历
├── StockBasic        # 股票基本信息
├── Daily             # 日线行情（不复权）
├── DailyBasic        # 每日指标（PE/PB/换手率等）
├── AdjFactor         # 复权因子
├── FundBasic         # 基金基本信息
├── FundDaily         # 基金日线
└── MoneyFlow         # 资金流向

财务报表表：
├── Income            # 利润表
├── BalanceSheet      # 资产负债表
├── CashFlow          # 现金流量表
├── FinaIndicator     # 财务指标
└── FinaAudit         # 财务审计

系统表：
├── SyncLog           # 同步日志
├── SyncCheckpoint    # 同步断点（用于增量恢复）
└── DataQualityLog    # 数据质量检查日志

回测表：
├── BacktestRun       # 回测运行记录
├── TradeRecord       # 交易记录
├── EquityCurve       # 净值曲线
└── PositionSnapshot  # 持仓快照
```

**额外创建的表（非ORM，SQL创建）：**
- `daily_qfq` - 前复权日线（1780万行）
- `daily_hfq` - 后复权日线（1780万行）

### 4. `data/sync/` - 数据同步模块

| 文件 | 用途 | 关联 |
|------|------|------|
| `engine.py` | **核心同步引擎**（356行），实现增量/全量同步、断点续传、数据写入 | 依赖 `tushare_client.py`、`config/data_sync_config.py` |
| `tushare_client.py` | Tushare API封装，含重试机制（指数退避）和速率限制（0.35s/次） | 被 `engine.py` 调用 |
| `scheduler.py` | APScheduler定时调度器，按cron表达式触发同步任务 | 依赖 `engine.py` |

**`engine.py` 核心流程：**

```
sync(table_name, mode)
  └── _sync_table(table_name, cfg, mode)
        ├── mode="once"      → _sync_once()      # 单次全量（如trade_cal）
        ├── mode="incremental" → _sync_incremental() # 增量同步（如daily）
        ├── mode="full"      → _sync_full()       # 全量重刷
        └── api_date_type="code" → _sync_by_code()  # 按股票代码循环（如财务报表）

_sync_incremental():
  1. _get_checkpoint() → 获取上次同步断点
  2. _get_trade_dates(since) → 从trade_cal获取待同步日期
  3. 遍历每个日期：
     ├── tushare_client.call(api, date=xxx) → 获取数据
     ├── _write_df() → 写入数据库（UPSERT）
     └── _update_checkpoint() → 更新断点
```

**`_write_df()` 写入逻辑：**
```
1. 创建临时表 _tmp_{table_name}
2. DataFrame → 临时表
3. 查询目标表列类型，过滤匹配列
4. 构建 INSERT ... ON CONFLICT DO UPDATE（UPSERT）
5. 删除临时表
```

### 5. `data/quality/` - 数据质量模块

| 文件 | 用途 | 关联 |
|------|------|------|
| `rules.py` | 质量规则定义（147行），含13条规则：空值检查、价格一致性、缺失日期等 | 被 `checker.py` 调用 |
| `checker.py` | 质量检查执行器，按规则检查数据并记录到 `DataQualityLog` | 依赖 `rules.py` |
| `reporter.py` | 质量报告生成器，查询最近7天的质量统计 | 依赖 `checker.py` |
| `sync_verifier.py` | 同步后验证：随机抽样日期，重新从Tushare获取，对比本地数据 | 被 `engine.py` 在同步后调用 |

### 6. `backtest/` - 回测引擎

| 文件 | 用途 | 关联 |
|------|------|------|
| **broker/** | | |
| `a_share.py` | A股券商模拟：佣金万三、印花税千一（卖出）、100股手数限制 | 被 `vbt_engine.py` 使用 |
| **engine/** | | |
| `vbt_engine.py` | **核心回测引擎**，编排数据加载→信号生成→组合模拟→结果持久化 | 依赖所有子模块 |
| `data_loader.py` | 从PostgreSQL加载OHLCV+基本面+复权因子到pandas | 依赖 `database/connection.py` |
| `result_extractor.py` | 从VectorBT Portfolio对象提取收益、回撤、夏普等指标 | 被 `vbt_engine.py` 调用 |
| **strategies/** | | |
| `base.py` | 抽象策略基类 `StrategyBase`，定义 `generate_signals()` 接口 | 被所有策略继承 |
| `macd.py` | MACD策略：MACD/信号线金叉买入，死叉卖出 | 继承 `base.py` |
| `ma_cross.py` | 双均线交叉策略：快线上穿慢线买入，下穿卖出 | 继承 `base.py` |
| `momentum.py` | 动量策略：买入N日涨幅前K的股票，排名下降卖出 | 继承 `base.py` |
| `registry.py` | 策略注册表，装饰器自动发现和注册策略 | 被 `vbt_engine.py` 使用 |
| **performance/** | | |
| `metrics.py` | 绩效指标计算：总收益、年化收益、最大回撤、夏普/索提诺/卡尔马比率 | 被 `result_extractor.py` 使用 |
| **storage/** | | |
| `persistor.py` | 回测结果持久化：保存运行记录、交易、净值曲线到PostgreSQL | 依赖 `database/connection.py` |
| `queries.py` | 命名SQL查询：回测汇总、净值曲线、交易列表、月度收益等 | 被 `backtest_api.py` 使用 |

**回测数据流：**
```
用户请求 → backtest_api.py → vbt_engine.py
  ├── data_loader.py → PostgreSQL(daily + daily_basic + adj_factor)
  ├── registry.get("macd") → macd.py.generate_signals()
  ├── vbt.Portfolio.from_signals() → VectorBT模拟
  ├── result_extractor.py → 提取指标
  └── persistor.py → 存入PostgreSQL(BacktestRun/TradeRecord/EquityCurve)
```

### 7. `web/` - Web应用

| 文件 | 用途 | 关联 |
|------|------|------|
| `app.py` | FastAPI应用入口，挂载路由、CORS、静态文件 | 依赖所有API模块 |
| **api/** | | |
| `admin_api.py` | 管理API（439行）：仪表盘数据、同步任务管理、断点管理、日志查看 | 依赖 `sync/engine.py` |
| `sync_api.py` | 同步API（113行）：触发同步、取消同步、查看状态 | 依赖 `sync/engine.py` |
| `backtest_api.py` | 回测API（138行）：运行回测、查看状态/结果、策略列表 | 依赖 `backtest/` |
| `quality_api.py` | 质量API：触发检查、查看日志和汇总 | 依赖 `quality/` |
| **static/** | | |
| `admin.html` | 管理后台SPA（462行），暗色主题，支持同步/回测/质量检查操作 | 调用所有API |

**API路由结构：**
```
/admin/api/
  ├── trigger/{table_name}    # 触发单表同步
  ├── tasks                   # 查看所有任务状态
  ├── checkpoints             # 查看同步断点
  └── logs/{module}           # 查看日志

/api/sync/
  ├── {table_name}            # 触发同步
  ├── all                     # 触发全部同步
  ├── status                  # 查看状态
  └── tables                  # 表列表

/api/backtest/
  ├── run                     # 运行回测
  ├── status/{run_id}         # 查看状态
  ├── history                 # 历史记录
  ├── strategies              # 策略列表
  └── trades/{run_id}         # 交易记录

/api/quality/
  ├── check/{table_name}      # 触发检查
  ├── log                     # 质量日志
  └── summary                 # 质量汇总
```

### 8. `visualization/grafana/` - Grafana仪表盘

| 文件 | 用途 |
|------|------|
| `dashboards/data_overview.json` | 数据概览：同步状态、表行数、质量检查结果、股票覆盖 |
| `dashboards/stock_kline.json` | **K线图**（1202行，12个面板）：不复权/前复权/后复权K线、成交量、估值指标、资金流向、财务指标 |
| `dashboards/backtest_analysis.json` | 回测分析：回测列表、净值曲线、交易分析、策略对比 |

**Stock K-Line 面板结构：**
```
Panel 1:  K-Line Price        (candlestick) - 不复权K线    FROM daily
Panel 2:  前复权 K-Line        (candlestick) - 前复权K线    FROM daily_qfq
Panel 3:  后复权 K-Line        (candlestick) - 后复权K线    FROM daily_hfq
Panel 4:  Volume              (bar)         - 成交量        FROM daily
Panel 5:  Valuation PE/PB     (timeseries)  - 估值指标      FROM daily_basic
Panel 6:  Turnover & Dividend (timeseries)  - 换手率/股息率  FROM daily_basic
Panel 7-12: Stats             (stat)        - 最新价/涨跌幅/成交量/成交额/PE/PB
```

### 9. `scripts/` - 运维脚本

| 文件 | 用途 |
|------|------|
| `init_db.py` | 初始化数据库：创建所有表、配置TimescaleDB |
| `run_sync.py` | CLI同步：同步单表/全部表 |
| `run_full_sync.py` | 全量重刷：重置断点到1991年，顺序同步所有表 |
| `create_adjusted_tables.py` | 创建前复权/后复权表结构 |
| `batch_adjusted.py` | 批量计算前复权/后复权价格（从daily+adj_factor） |
| `recover_pre2003_daily.py` | 恢复2003年以前缺失的日线数据 |
| `run_backtest_all.py` | 全量历史回测：选前50只股票，运行所有策略 |
| `check_*.py` | 各类数据检查脚本（状态/类型/schema/覆盖度） |
| `verify_adjusted.py` | 验证复权数据正确性 |

### 10. `utils/` - 工具模块

| 文件 | 用途 | 关联 |
|------|------|------|
| `logger.py` | 统一日志模块：按模块映射到不同日志文件（sync/backtest/data/quality） | 被所有模块使用 |
| `trade_calendar.py` | 交易日历工具：`get_trade_dates()`、`is_trade_date()` | 被回测引擎使用 |

---

## 数据流架构

```
Tushare API
    │
    ▼
data/sync/engine.py ──→ PostgreSQL (daily, daily_basic, adj_factor, ...)
    │                          │
    │                          ├── daily_qfq (前复权，SQL计算)
    │                          └── daily_hfq (后复权，SQL计算)
    │
    ▼
data/quality/checker.py ──→ DataQualityLog
    │
    ▼
data/quality/sync_verifier.py ──→ 对比Tushare vs 本地数据
    │
    ▼
backtest/engine/vbt_engine.py ──→ VectorBT回测
    │         │
    │         ├── strategies/ (信号生成)
    │         ├── broker/a_share.py (费用模拟)
    │         └── storage/persistor.py ──→ PostgreSQL(backtest_run, trade_record, equity_curve)
    │
    ▼
web/api/ ──→ FastAPI REST API
    │
    ▼
Grafana (visualization/) ──→ 仪表盘展示
```

---

## 数据库表关系

```
stock_basic (股票基本信息)
    │
    ├── daily (日线行情) ──┐
    │                      ├── adj_factor (复权因子)
    │                      │       ├── daily_qfq (前复权)
    │                      │       └── daily_hfq (后复权)
    │                      │
    ├── daily_basic (每日指标)
    ├── moneyflow (资金流向)
    ├── income (利润表)
    ├── balancesheet (资产负债表)
    ├── cashflow (现金流量表)
    ├── fina_indicator (财务指标)
    └── fina_audit (财务审计)

fund_basic (基金基本信息)
    └── fund_daily (基金日线)

trade_cal (交易日历) ──→ 被所有增量同步使用（确定同步日期范围）

backtest_run (回测记录)
    ├── trade_record (交易记录)
    ├── equity_curve (净值曲线)
    └── position_snapshot (持仓快照)

sync_log (同步日志)
sync_checkpoint (同步断点) ──→ 支持增量同步断点续传
data_quality_log (质量检查日志)
```

---

## 配置文件

### `.env` (环境变量)
```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=quant_db
DB_USER=postgres
DB_PASSWORD=postgres
TUSHARE_TOKEN=596dc52bf2356c6241077de51b61fea2c0ceeb1eebd6d78ec88e9832
LOG_DIR=D:\code\Python\ai_quant_python\logs
```

### Grafana配置
- `conf/custom.ini`: 端口8080, 时区Asia/Shanghai, time_picker配置
- `conf/provisioning/datasources/postgresql.yaml`: PostgreSQL数据源
- `conf/provenvisioning/dashboards/grafana.yml`: 仪表盘自动加载

---

## 运行方式

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 初始化数据库
python scripts/init_db.py

# 3. 启动数据同步
python scripts/run_full_sync.py

# 4. 启动Web管理后台
python -m uvicorn web.app:app --port 8088

# 5. 启动Grafana (Windows服务)
# 访问 http://localhost:8080

# 6. 运行回测
python run_backtest_task.py
```

---

## 复权价格计算公式

| 类型 | 公式 | 说明 |
|------|------|------|
| 不复权 (daily) | 原始价格 | Tushare直接返回 |
| 前复权 (daily_qfq) | `raw_price × (adj_factor / latest_adj_factor)` | 最新价=原始价，历史价向前调整 |
| 后复权 (daily_hfq) | `raw_price × adj_factor` | 上市首日价=原始价，历史价向后调整 |

示例（000001.SZ 平安银行，2026-06-18）：
- 不复权：O=10.74, C=10.52
- 前复权：O=10.74, C=10.52（最新价不变）
- 后复权：O=1492.95, C=1462.36（adj_factor=139.008）

---

## 文件依赖关系图

```
config/settings.py ←── 所有模块
config/data_sync_config.py ←── sync/engine.py, scripts/*

data/database/connection.py ←── 所有数据操作
data/database/models.py ←── sync/engine.py, web/api/*, backtest/storage/*
data/sync/engine.py ←── web/api/admin_api.py, web/api/sync_api.py, scripts/*
data/sync/tushare_client.py ←── sync/engine.py
data/quality/rules.py ←── quality/checker.py
data/quality/checker.py ←── web/api/quality_api.py

backtest/strategies/base.py ←── 所有策略
backtest/strategies/registry.py ←── backtest/engine/vbt_engine.py
backtest/engine/vbt_engine.py ←── web/api/backtest_api.py
backtest/broker/a_share.py ←── backtest/engine/vbt_engine.py
backtest/storage/persistor.py ←── backtest/engine/vbt_engine.py

web/app.py ←── uvicorn入口
web/api/*.py ←── admin.html (前端调用)
```

---

*Generated: 2026-06-19*
