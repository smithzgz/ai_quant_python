# 三框架回测对比文档：Backtrader vs VectorBT vs Qlib

## 一、最终结论

| 模型 | 收益率 | 与Qlib差异 | 耗时 |
|------|--------|-----------|------|
| **Qlib (numpy循环)** | 30.89% | 基准 | 0.043s |
| **VectorBT (from_orders)** | 30.89% | -0.00% | 0.942s |
| **Backtrader (独立运行)** | 29.07% | -1.81% | 3.632s |

**Qlib 和 VectorBT 结果完全一致 (差异 < 0.01%)**，Backtrader 差异 -1.81% 来自信号生成机制不同。

---

## 二、统一设计

### 2.1 核心原则

```
一次预计算 → 三个框架共用
```

1. **统一信号**: SMA(5) > SMA(20) 金叉买入，死叉卖出
2. **统一共享资金池**: `alloc = cash / n_stocks`，顺序执行，`int()` 整数股
3. **预计算 order size**: 所有框架使用相同的订单大小
4. **VectorBT**: `from_orders()` 执行预计算 size（绕过 FIFO 资金模型）
5. **Qlib**: numpy 循环直接执行预计算逻辑
6. **Backtrader**: 独立运行，对比验证

### 2.2 共享资金池逻辑

```python
# 顺序处理每只股票 (与 Backtrader next() 一致)
for j in range(n_stocks):
    # 卖出
    if prev_sig == 1 and curr_sig == 0:
        cash += shares[j] * sell_price * (1 - fees)
        shares[j] = 0
    
    # 买入 (立即扣款)
    elif prev_sig == 0 and curr_sig == 1:
        alloc = cash / n_stocks          # 递减现金
        size = int(alloc / buy_price)    # 整数股
        shares[j] = size
        cash -= cost                     # 立即扣款
```

---

## 三、框架差异详解

### 3.1 信号生成差异 (Backtrader vs Qlib/VectorBT)

| 方面 | Backtrader | Qlib/VectorBT |
|------|------------|---------------|
| **指标** | `CrossOver(fast_ma, slow_ma)` | `(fast_ma > slow_ma)` |
| **NaN 处理** | CrossOver 内部处理 | 直接比较 (NaN > x = False) |
| **信号时机** | nextstart=bar 442 | nextstart=bar 442 |
| **交易数量** | 355 buys | 358 buys |

**差异来源**: Backtrader 的 `CrossOver` 指标在 NaN 处理上与手动 `(fast_ma > slow_ma)` 略有不同，导致 3 笔额外交易。

### 3.2 VectorBT 的 `cash_sharing` 问题

| 模式 | 行为 | 适用场景 |
|------|------|----------|
| `cash_sharing=False` | 每只股票独立 init_cash | 独立资金池 |
| `cash_sharing=True` | FIFO 模型 (一次只持一个仓位) | ❌ 不适用 |
| `from_orders()` + `cash_sharing=True` | 按预计算 size 执行 | ✅ 共享资金池 |

**解决方案**: 使用 `from_orders()` 绕过 VectorBT 的 FIFO 资金模型。

### 3.3 `int()` 截断

所有框架都使用 `int()` 截断为整数股：
- `size = int(alloc / buy_price)`
- 由于 Qlib/VectorBT 使用相同的预计算 size，截断结果完全一致
- Backtrader 因信号不同，截断结果略有差异

---

## 四、实现细节

### 4.1 预计算函数

```python
def calc_shared_pool_sizes(close_arr, fast_ma_arr, slow_ma_arr, nextstart,
                           total_cash, n_stocks, fees, slippage):
    """预计算共享资金池的所有 order size"""
    cash = float(total_cash)
    shares = np.zeros(n_stocks)
    order_sizes = np.zeros((n_dates, n_stocks))
    
    for i in range(1, n_dates):
        for j in range(n_stocks):
            # 信号判断
            prev_sig = 1 if fast_ma_arr[i-1,j] > slow_ma_arr[i-1,j] else 0
            curr_sig = 1 if fast_ma_arr[i,j] > slow_ma_arr[i,j] else 0
            
            # 卖出
            if shares[j] > 0 and prev_sig == 1 and curr_sig == 0:
                cash += shares[j] * sell_price * (1 - fees)
                order_sizes[i,j] = -shares[j]
                shares[j] = 0
            
            # 买入
            elif shares[j] == 0 and prev_sig == 0 and curr_sig == 1:
                alloc = cash / n_stocks
                size = int(alloc / buy_price)
                if size > 0:
                    order_sizes[i,j] = size
                    shares[j] = size
                    cash -= size * buy_price * (1 + fees)
    
    return order_sizes, values
```

### 4.2 VectorBT 执行

```python
pf_vbt = vbt.Portfolio.from_orders(
    close=close_df,
    size=order_sizes_df,      # 预计算的 size
    price=close_df,
    fees=FEES,
    slippage=SLIPPAGE,
    cash_sharing=True,
    init_cash=TOTAL_CASH,
)
```

### 4.3 Qlib 执行

Qlib = 预计算函数的直接复用（numpy 循环）。

---

## 五、文件说明

| 文件 | 说明 |
|------|------|
| `three_framework_shared_pool.py` | 三框架对比主脚本 |
| `dual_ma_compare.py` | Qlib vs VectorBT 教程 |
| `run_jlp_backtest.py` | JLP 数据回测 |
| `three_framework_diff.md` | 本文档 |

---

## 六、常见问题

### Q: 为什么 Qlib 和 VectorBT 结果完全一致？

A: 因为它们使用**完全相同的预计算 order size**。VectorBT 的 `from_orders()` 直接执行这些 size，不使用自己的资金管理逻辑。

### Q: 为什么 Backtrader 有 -1.81% 差异？

A: 差异来自**信号生成机制不同**：
- Backtrader 用 `CrossOver` 指标（内部 NaN 处理不同）
- Qlib/VectorBT 用 `(fast_ma > slow_ma)`（直接比较）
- 导致交易数量不同（355 vs 358 笔）

### Q: 如何让 Backtrader 也一致？

A: 需要让 Backtrader 使用与 Qlib/VectorBT 相同的信号。可以通过：
1. 预计算信号，传入 Backtrader
2. 或修改 Backtrader 的信号生成逻辑

### Q: 浮点股问题解决了吗？

A: 是的。所有框架都使用 `int()` 截断为整数股。Qlib 和 VectorBT 的截断结果完全一致（因为使用相同的预计算 size）。

---

## 七、性能对比

| 指标 | Qlib | VectorBT | Backtrader |
|------|------|----------|------------|
| **耗时** | 0.043s | 0.942s | 3.632s |
| **内存** | 低 (numpy) | 高 (DataFrame) | 低 (事件驱动) |
| **适用场景** | 快速验证 | 参数优化 | 复杂策略 |

---

*最后更新: 2026-06-30*
