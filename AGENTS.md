# AGENTS.md — AI 开发协作规范

> 中国A股量化系统：Tushare 数据同步 → PostgreSQL/TimescaleDB → VectorBT 回测 → FastAPI + Grafana。
> 项目结构见 `PROJECT_STRUCTURE.md`；同步模块操作手册见 `skills/tushare_sync.md`；分钟数据规划与使用纪律见 `MINUTE_DATA_PLAN.md`。

## 一、核心工作流（必须遵守）

### 1. 先设计，再开发，方案需人工审核
- 非平凡改动（新功能、跨模块、schema、API 契约、回测语义）先输出设计：目标、备选方案、影响面、测试计划、回滚方式，**等人工确认后才动手**。
- 平凡改动（错别字、日志、注释）可直接执行。

### 2. 修改前先做影响分析
- 列出所有调用方：Grep 引用、web/api、scripts/。
- **沉默消费方三件套——代码改动前后都要检查是否需要同步修改**：
  - **Grafana**：`visualization/grafana/dashboards/*.json` 内嵌 SQL 直查数据库，改表名/列名/数据单位口径前必须 Grep；
  - **定时任务**：`data/sync/scheduler.py` 按 `config/data_sync_config.py` 的 schedule/sync_func 生成任务，改引擎钩子/任务定义/同步行为时检查调度链路；
  - **Web 页面**：`web/static/admin.html` 直接消费 `web/api/*` 返回字段，改 API 契约/字段单位/状态枚举时同步核对前端。
- **Schema 三处定义陷阱**：表结构同时存在于 `data/database/models.py`（ORM）、`config/data_sync_config.py`（fields）、`scripts/create_*.py`（原生 SQL），改一处必查另两处。
- 写入路径（upsert 语义、checkpoint）与读取路径（查询、回测加载）都要过一遍。

### 3. 重要环节必须有用例保证
资金费用计算、复权价格、信号生成、数据写入（幂等/断点）、绩效指标口径——改动必须附可复现的验证，不允许"改完看起来对"。当前无 pytest，按三级验证：
1. **合成数据最小复现**：不依赖 DB，直接构造输入验证逻辑；
2. **端到端**：`python run_backtest_task.py` 跑通；
3. **落库抽查**：trade_records/equity_curves 抽样，核对时间戳、费用、百分比单位。

### 4. 开发完成后清理中间文件
- 临时验证脚本放系统临时目录，**结束后删除**，禁止留在仓库或工作目录。
- 新增正式脚本放 scripts/ 下。

## 二、架构不变量（改代码不许破坏）

| 不变量 | 规则 |
|--------|------|
| `daily_qfq`/`daily_hfq` 是**视图** | 禁止 INSERT、禁止重建物化表；复权一律查询时计算 |
| 回测组合语义 | `cash_sharing=True` + `size_type='value'` 等权分配；净值曲线为组合总值（分组后 value() 是 Series）；收益/回撤/return_pct 统一存百分比 |
| vectorbt 1.0 列名 | trades 用 `Entry Timestamp`/`Exit Timestamp`/`Entry Fees`/`Exit Fees`（不是旧版 `Entry Index`/`Fees`） |
| SQL 一律参数化 | 禁止 f-string/format 拼 SQL |
| 密钥管理 | token 等只在 `.env`；任何情况下不写入代码、文档、提交 |

## 三、环境注意事项

- **Python 是共享环境**：`C:\veighna_studio`（vnpy 发行版）。pip install/downgrade 前先 `pip show <pkg>` 查 `Required-by`；新代码用到新依赖时同步写进 `requirements.txt`。
- PowerShell 5.1：`&&` 不可用；`rg` 不存在（用 Grep 工具）；控制台中文乱码属正常；复杂 python 内联脚本写临时文件再执行（避免转义问题）。
- 无 lint/test 框架，最低验证：`python -m py_compile <改动文件>`。

## 四、验证安全红线

- 启动 web app / TestClient 会拉起 APScheduler，且 `misfire_grace_time=3600`——**错过触发窗的定时任务会在启动瞬间立即执行真实同步**。验证 Web 层必须先 stub `data.sync.scheduler.create_scheduler`。
- 跑真实同步任务消耗 Tushare API 配额，验证时限定单表、小日期范围。
- 数据库破坏性操作（DROP/TRUNCATE/批量删除/重置 checkpoint）即使数据可推导，也必须**先征得人工确认**再执行。

## 五、数据库操作规范

- 大表（daily/adj_factor 均 1800 万行+）避免 `COUNT(*)`，行数估算用 `pg_stat_user_tables.n_live_tup`。
- 结构迁移参照 `scripts/migrate_adjusted_views.py` 的模式：迁移 + `--verify` 自校验。
- 质检规则新增走 `data/quality/rules.py`，写法必须是 SQL 聚合下推，禁止 `SELECT * LIMIT N` 拉 pandas。

## 六、Git 与文档纪律

- **不主动 commit/push**，除非用户明确要求。工作区常有用户未提交的变更——动手前 `git status` 记录基线，汇报时明确区分"我的改动"与"用户既有变更"。
- 提交信息风格：简短中文或 `fix:`/`docs:` 前缀。
- 文档同步义务：目录/文件级变更（含新增 scripts/）更新 `PROJECT_STRUCTURE.md`；数据链路变更对照 `PLAN.md`；分钟数据相关工作以 `MINUTE_DATA_PLAN.md` 为准；sync 模块行为变更同步 `skills/tushare_sync.md`。
