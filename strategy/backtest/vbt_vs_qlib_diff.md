# VectorBT vs Qlib 框架对比文档

## 一、框架简介

| 特性 | VectorBT | Qlib |
|------|----------|------|
| **定位** | 轻量级回测库 | 量化投资平台 |
| **开发者** | 社区维护 | 微软开源 |
| **核心优势** | 向量化计算、简单易用 | 机器学习支持、完整生态 |
| **安装难度** | 简单（pip install） | 复杂（需初始化数据目录） |
| **学习曲线** | 低 | 高 |
| **适用场景** | 快速验证策略 | 深度研究、模型训练 |

---

## 二、核心差异

### 2.1 init_cash 含义不同

这是最容易踩的坑：

```python
# VectorBT: init_cash 是每只股票的初始资金
pf = vbt.Portfolio.from_signals(
    close_df,      # 50 只股票
    entries=entries,
    exits=exits,
    init_cash=20000,  # 每只股票 20,000，总资金 = 20,000 × 50 = 1,000,000
)

# Qlib 手动实现: init_cash 通常理解为总资金
init_cash = 1_000_000  # 总资金
cash_per_stock = init_cash / n_stocks  # 每只股票分 20,000
```

**结论**：要让结果一致，Qlib 的 `cash_per_stock` 必须等于 VectorBT 的 `init_cash`。

### 2.2 买入条件不同

```python
# VectorBT: 已有持仓时不会重复买入
# 它内部会检查 cash 是否足够

# Qlib 手动实现: 需要手动加条件
buy_mask = entry & valid & (cash > 0)  # 必须加 cash > 0
```

**后果**：如果不加 `cash > 0` 条件，会在已有持仓时再次买入，导致 cash 被错误消耗。

### 2.3 信号处理方式不同

| 方面 | VectorBT | Qlib 手动实现 |
|------|----------|---------------|
| **信号类型** | entries/exits 布尔矩阵 | 需手动转换 |
| **交叉检测** | 内置函数 | 需手动计算 |
| **信号对齐** | 自动处理 | 需手动对齐日期 |

```python
# VectorBT 的 entries/exits 生成方式
entries, exits = vbt.MA.run_combs(...)  # 内置函数

# Qlib 手动实现的 entries/exits 生成
fast_ma = close_df.rolling(5).mean()
slow_ma = close_df.rolling(20).mean()
signal = (fast_ma > slow_ma).astype(int)
prev_signal = signal.shift(1)
entries = (prev_signal == 0) & (signal == 1)  # 金叉
exits = (prev_signal == 1) & (signal == 0)    # 死叉
```

### 2.4 费用计算方式

两者的费用计算逻辑一致，但需要注意参数含义：

```python
# VectorBT
pf = vbt.Portfolio.from_signals(
    ...,
    fees=0.001,      # 单边手续费 0.1%
    slippage=0.001,  # 滑点 0.1%
)

# Qlib 手动实现
buy_price = price * (1 + slippage)   # 买入价 = 收盘价 × (1 + 0.1%)
sell_price = price * (1 - slippage)  # 卖出价 = 收盘价 × (1 - 0.1%)
shares = cash / buy_price
shares = shares * (1 - fees)         # 手续费从份额扣除
```

### 2.5 NaN 处理

```python
# VectorBT: 自动处理 NaN

# Qlib 手动实现: 需要手动处理
valid = ~np.isnan(price)
buy_mask = entry & valid & (cash > 0)

# 计算价值时也要处理 NaN
safe_price = np.where(np.isnan(price), 0, price)
values[i] = cash + shares * safe_price
```

---

## 三、让结果完全一致的 checklist

| 步骤 | 检查项 | 说明 |
|------|--------|------|
| 1 | 数据源相同 | 使用相同的 DataFrame |
| 2 | 信号生成相同 | 相同的 entries/exits 逻辑 |
| 3 | init_cash 含义对齐 | VectorBT 是每只股票，Qlib 需要除以股票数 |
| 4 | 买入条件加 cash > 0 | 防止重复买入 |
| 5 | 费用参数一致 | fees 和 slippage 相同 |
| 6 | NaN 处理一致 | 价格为 NaN 时不交易 |
| 7 | 价值计算一致 | 使用相同的公式 |

---

## 四、性能对比

### 4.1 测试环境

- 数据：50 只 A 股，2020-01-01 ~ 2026-06-30（1569 个交易日）
- 策略：双均线（5 日/20 日）
- 费用：手续费 0.1% × 2，滑点 0.1%

### 4.2 结果

| 指标 | VectorBT | Qlib（numpy） | 差异 |
|------|----------|---------------|------|
| **总收益率** | 26.35% | 26.34% | 0.01% |
| **年化收益率** | 3.83% | 3.83% | 0.00% |
| **最大回撤** | -22.99% | -22.99% | 0.00% |
| **夏普比率** | 0.3596 | 0.3285 | 0.0311 |
| **胜率** | 49.27% | 49.71% | 0.45% |
| **最终价值** | ¥1,263,464 | ¥1,263,405 | ¥58.57 |
| **耗时** | 7.02 秒 | 0.045 秒 | **Qlib 快 157 倍** |

### 4.3 差异分析

**完全一致的指标**（差异 < 0.01%）：
- 总收益率
- 年化收益率
- 最大回撤

**有微小差异的指标**：
- 夏普比率（计算方法不同）
- 胜率（VectorBT 按交易统计，Qlib 按日统计）

---

## 五、速度差异原因

| 原因 | VectorBT | Qlib（numpy） |
|------|----------|---------------|
| **计算方式** | 完整的 Portfolio 类 | 纯 numpy 循环 |
| **功能开销** | 止损、仓位管理、交易记录 | 只计算核心逻辑 |
| **内存管理** | DataFrame 操作 | numpy 数组操作 |

**VectorBT 慢的原因**：
1. Portfolio 类包含大量附加功能（止损、仓位管理、交易记录等）
2. 每次交易都需要记录详细信息
3. 内部有大量的数据验证和转换

**Qlib 快的原因**：
1. 只计算核心逻辑（买入/卖出/价值）
2. 使用 numpy 数组，避免 DataFrame 开销
3. 没有附加功能的开销

---

## 六、使用建议

| 场景 | 推荐 | 原因 |
|------|------|------|
| **快速验证策略** | VectorBT | 简单、功能完整 |
| **生产环境回测** | Qlib（numpy） | 速度快、可定制 |
| **机器学习研究** | Qlib | 内置因子库、模型支持 |
| **多策略对比** | VectorBT | 内置对比工具 |
| **大规模回测** | Qlib（numpy） | 速度优势明显 |

---

## 七、常见错误

### 错误 1：init_cash 含义混淆

```python
# 错误：用总资金作为 VectorBT 的 init_cash
pf = vbt.Portfolio.from_signals(..., init_cash=1_000_000)  # 每只股票 100 万！

# 正确：用每只股票的资金
pf = vbt.Portfolio.from_signals(..., init_cash=20_000)  # 每只股票 2 万
```

### 错误 2：忘记加 cash > 0 条件

```python
# 错误：会在已有持仓时再次买入
buy_mask = entry & valid

# 正确：只在有现金时买入
buy_mask = entry & valid & (cash > 0)
```

### 错误 3：NaN 处理不当

```python
# 错误：NaN 价格会导致计算错误
values[i] = cash + shares * price  # price 是 NaN 时结果也是 NaN

# 正确：处理 NaN 价格
safe_price = np.where(np.isnan(price), 0, price)
values[i] = cash + shares * safe_price
```

---

## 八、文件说明

| 文件 | 说明 |
|------|------|
| `dual_ma_identical.py` | 完全一致的双均线策略对比脚本 |
| `dual_ma_compare.py` | 早期版本（功能不完整） |
| `vbt_vs_qlib_diff.md` | 本文档 |
