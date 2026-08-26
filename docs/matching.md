# 撮合与账本口径

> 适用版本：v0.1。契约层级见 [`docs/design/mvp-contract.md`](design/mvp-contract.md) §3.4；本文给出可操作的边界与代码位置。

## 1. 同批撮合顺序

单个 `OPEN_MATCH(D)` batch 内**所有 SELL 先撮合、再撮合 BUY**（A 股「卖出资金当日可用」语义）：

```text
OPEN_MATCH(D)
    ├── SELL 队列（按策略提交顺序；缺钱 / 缺券直接拒，不滚动）
    │       └── 卖出回款累加到 running_cash
    └── BUY  队列（按策略提交顺序；按整手向下取整；现金不足直接拒）
            └── 成本从 running_cash 扣除
```

资金检查走 **滚动现金**（running_cash）：SELL 净回款先增、BUY 成本后扣；同批 SELL 失败的 BUY 不会回滚。这保证「卖出资金当日可用」语义。

代码位置：`src/hqbacktest/engine/broker.py`（`match()`、`SimulatedBroker.match`）。

## 2. 数量与整手

| 维度 | v0.1 决定 | 备注 |
| --- | --- | --- |
| BUY 整手取整 | 按 100 股向下取整 | `LOT_SIZE=100`；零股静默截到整手违反契约 |
| SELL 数量 | 允许任意正整数股（含零股） | 仅 BUY 整手；SELL 严禁静默截到整手 |
| 清仓零股 | `order_target(symbol, 0)` 必须能清掉含零股的持仓 | 走 `intents.target_quantity_for_value(0) → 0` |
| `order(sym, -N)` | 对任意正整数 N 提交原数量 | 不整手 |
| T+1 可卖不足 | **整单拒绝**（`RejectReason.INSUFFICIENT_SHARES`） | 不支持部分截断 |

代码位置：`src/hqbacktest/engine/intents.py`、`src/hqbacktest/engine/rules.py`。

## 3. 费用量化

| 字段 | 四舍五入方式 | 精度 |
| --- | --- | --- |
| 现金 / 费用 / 净值 | `ROUND_HALF_EVEN` | `quantize_cash = Decimal('0.01')` 元 |
| 价格 / 成交价 / 成本价 | `ROUND_HALF_EVEN` | `quantize_price = Decimal('0.0001')` 元 |

与券商「四舍五入到分」存在 **1 分级** 差异（半数情形不同），属契约口径，不引入误差补偿。

代码位置：`src/hqbacktest/util/money.py`。

## 4. 费用方向

| 费用 | BUY | SELL |
| --- | --- | --- |
| 佣金 | ✓（`commission_rate`） | ✓ |
| 印花税 | **0**（`Fill.__post_init__` 拒绝非零） | ✓（`stamp_tax_rate`） |
| 过户费 | 可选（默认 0） | 可选 |

`Fill.BUY` 携带非零 `stamp_tax` 是契约违反，构造期即抛错。这条约束保证 `costs.csv` 与账本一致。

代码位置：`src/hqbacktest/domain/fill.py`、`src/hqbacktest/engine/cost.py`。

## 5. realized_pnl 口径

`realized_pnl` **不含任何费用**，仅 `(sell_price - avg_cost) × quantity`：

- 费用（commission / stamp_tax / other_fee）只走现金账，不混入 `realized_pnl`。
- 同日同价「先买后卖」与「先卖后买」时，`realized_pnl` 在费用外相同，**不含费用的现金额**依提交顺序不同（SELL 先成交会先释放资金再扣 BUY 成本）。
- 持有期跨越多个交易日时，`avg_cost` 是该日日终的滚动加权平均成本，由 broker 在每次成交后写入 `Position.avg_cost`。

代码位置：`src/hqbacktest/engine/portfolio.py`、`src/hqbacktest/domain/position.py`。

## 6. CLI 与引擎一致

`hqbacktest run` 的 `[capital].initial_cash` 字段：

| 类型 | 是否接受 | 错误码 |
| --- | --- | --- |
| `int` / `str` / `Decimal` | ✓ | — |
| `float`（含 TOML 字面 `100000.0`） | ✗ | exit 2 |
| `nan` / `+inf` / `-inf` | ✗ | exit 2 |

这与 `BacktestConfig` 字段的严格度对齐，阻止浮点金额进入账本。

详见 [`docs/cli.md`](cli.md) §「错误信息」。

## 7. 手算回归

| 场景 | 测试位置 |
| --- | --- |
| 同批 SELL 先于 BUY + 滚动现金 | `tests/engine/test_matching.py` |
| BUY 整手向下、SELL 不整手、`order_target(0)` 清零股 | `tests/engine/test_intents.py` |
| `Fill.BUY` 拒绝非零 `stamp_tax` | `tests/domain/test_fill.py` |
| `realized_pnl` 不含费用 | `tests/engine/test_portfolio_realized_pnl.py` |
| CLI `initial_cash` 拒绝 float / nan / inf | `tests/cli/test_config.py` |
