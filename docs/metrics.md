# 净值与绩效指标口径

> 适用版本：v0.1。契约层级见 [`docs/design/mvp-contract.md`](design/mvp-contract.md) §3.5；输出文件 schema 见 [`docs/output.md`](output.md)。

## 1. 数据来源

`PerformanceMetrics` 不重新从 `total_equity` 推导日收益，而是**直接读 `EquityPoint.daily_return`**——这是引擎在日终写入的字段。这避免了「零种子」推导在首日真实 P&L 上的丢失。

```text
BacktestEngine._snapshot_equity(D)
    └── 写入 EquityPoint { total_equity, daily_return, drawdown }
            │
            ▼
PerformanceMetrics.compute_metrics(equity_curve, initial_cash, MetricsConfig)
    └── 直接消费 EquityPoint.daily_return 与 EquityPoint.drawdown
```

代码位置：`src/hqbacktest/engine/metrics.py`（顶部注释含完整公式）、`src/hqbacktest/engine/equity.py`。

## 2. 首日 P&L 进入曲线

`equity_curve[0]` 首日不再硬编码 `daily_return=0` / `drawdown=0`：

| 字段 | 首日（D = start_date） | 后续日 |
| --- | --- | --- |
| `daily_return` | `total_equity[0] / initial_cash - 1` | `(total_equity[t] - total_equity[t-1]) / total_equity[t-1]` |
| `drawdown` | `(initial_cash - total_equity[0]) / initial_cash`（首日下跌时为正） | `(running_peak - total_equity[t]) / running_peak` |

**running peak 序列**：`max(initial_cash, 历史 total_equity)`。首日跌幅进入回撤峰值序列，后续日的回撤以「比 initial_cash 与历史最高 equity 的最大值还低多少」衡量。

```text
running_peak[t] = max(initial_cash, total_equity[0], total_equity[1], ..., total_equity[t])
drawdown[t]    = (running_peak[t] - total_equity[t]) / running_peak[t]   # ∈ [0, 1)
```

满足恒等式：`∏(1 + daily_return) == 1 + total_return`（Decimal 精度内）。

## 3. 波动率与夏普：样本不足返回 `None`

| 指标 | 序列长度 < 2 | 真正 0 波动率 | 正常 |
| --- | --- | --- | --- |
| `daily_volatility` | `None` + note | `Decimal('0')` | `Decimal(str(stdev(returns, ddof=1)))` |
| `annualized_volatility` | `None` + note | `Decimal('0')` | `daily_volatility * sqrt(annual_trading_days)` |
| `sharpe_ratio` | `None` + note | `None` + note（零分母） | `(annualized_return - risk_free_rate) / annualized_volatility` |

**禁止**把样本不足错报为 `Decimal('0')`。`None` 会在 `summary.json` 的 `metrics.notes` 字段留痕。

`annual_trading_days` 默认 252，`risk_free_rate` 默认 0，二者均走 `MetricsConfig` 写入 `summary.json`，可追溯。

## 4. 幂运算桥接

```text
(1 + total_return) ** (n / annual_trading_days)
```

的实现走 `Decimal(str(float(growth) ** float(exponent)))`，**禁止 `Decimal(float(...))`**。这避免了二进制浮点被直接继承到 Decimal 字段后污染 `summary.json`。

代码位置：`src/hqbacktest/engine/metrics.py` 中的 `_annualized_return_power_bridge`。

## 5. Decimal 量化

所有 `float` 桥接的 Decimal 输出统一 quantize 到 `Decimal('0.000000000001')`（1e-12 元），保证 `summary.json` 字段形态干净。真正从账本推导的字段（`total_return`、`realized_pnl` 等）保留 `quantize_cash`（0.01 元）粒度。

## 6. `positions.sellable_quantity` 结转后

`positions[date].sellable_quantity` 显示的是 **D 行快照的 D+1 起始时可卖数**——即 `settle_t1` 已结算、T+1 释放后的数量，而不是「D 当日还剩多少」。

```text
BAR_CLOSE(D):
    broker.match()           # 撮合，含 BUY（计入当日买入，T+1 才可卖）
    portfolio.settle_t1(D)   # 把 D 当日买入转结为 D+1 可卖
    snapshot（D + 1 起始）   # positions[D].sellable_quantity = 已结转
```

代码位置：`src/hqbacktest/engine/portfolio.py::settle_t1`。

## 7. 边界与错误

- 持仓标的在某日无有效收盘价 → 引擎运行失败并记录 `DATA_ERROR`（契约 §4：不使用前收、插值或静默按零估值）。
- `risk_free_rate` / `annual_trading_days` / 样本不足的说明均落到 `summary.json.metrics.notes`。

## 8. 手算回归

| 场景 | 测试位置 |
| --- | --- |
| `daily_return` / `drawdown` 首日锚定 `initial_cash` | `tests/engine/test_metrics.py::test_first_day_daily_return_and_drawdown` |
| 恒等式 `∏(1+daily_return) == 1+total_return` | `tests/engine/test_metrics.py::test_identity_product_equals_one_plus_total_return` |
| 2 日 -9% / +5.5% → `daily_volatility` ≈ 0.10253 | `tests/engine/test_metrics.py::test_two_day_volatility_known_value` |
| 样本不足返回 `None`（非 0） | `tests/engine/test_metrics.py::test_insufficient_samples_returns_none` |
| `positions.sellable_quantity` 结转后口径 | `tests/engine/test_metrics.py::test_sellable_quantity_post_settle` |
