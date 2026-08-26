# BacktestResult 输出

> 适用版本：v0.1。本文档说明 `BacktestResult.save(dir)` 写出的文件 schema 与 `PerformanceMetrics` 字段。指标算法口径见 [`docs/metrics.md`](metrics.md)，CLI 输出目录与命名见 [`docs/cli.md`](cli.md) §3。

## 1. 文件总览

```text
<directory>/
├── config.toml              # 你传入的原始 TOML（精确字节）
├── run_metadata.json        # hqbacktest / Python / 平台 / 时间戳 / git commit
├── events.jsonl             # 引擎事件日志（每行一条 JSON）
├── equity_curve.csv
├── orders.csv
├── fills.csv
├── positions.csv
├── costs.csv
└── summary.json
```

每个文件都可独立读出，互不重叠。

## 2. 表格 schema（CSV）

### 2.1 `equity_curve.csv`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `date` | `str` (YYYYMMDD) | 回测日 |
| `cash` | `Decimal` | 当日现金（含冻结） |
| `market_value` | `Decimal` | 持仓市值（按 D 有效 close 估值） |
| `total_equity` | `Decimal` | `cash + market_value` |
| `daily_return` | `Decimal` | 首日 `total_equity[0] / initial_cash - 1`；后续日 `(total_equity[t] - total_equity[t-1]) / total_equity[t-1]` |
| `drawdown` | `Decimal` | `(running_peak - total_equity[t]) / running_peak`，running_peak 序列以 `initial_cash` 为起点 |

首日 P&L 进入曲线的细节见 [`docs/metrics.md`](metrics.md) §2。

### 2.2 `orders.csv`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `order_id` | `str` | 引擎分配，UUID |
| `date` | `str` | 订单创建日 |
| `phase` | `str` | 创建所在阶段（`BEFORE_TRADING_START` / `BAR_CLOSE`） |
| `symbol` | `str` | `600000.SH` 等 |
| `side` | `str` | `BUY` / `SELL` |
| `quantity` | `int` | 已 BUY 整手向下取整；SELL 原值 |
| `type` | `str` | `MARKET`（v0.1 唯一） |
| `status` | `str` | `ACCEPTED` / `PENDING` / `FILLED` / `REJECTED` / `CANCELLED` |
| `avg_fill_price` | `Decimal` 或空 | 全部成交后的加权均价 |
| `filled_quantity` | `int` | 累计成交股数 |
| `fill_ids` | `str` | 逗号分隔的 `fill_id` 列表 |
| `reject_reason` | `str` 或空 | 拒绝原因枚举值 |

`OUT_OF_UNIVERSE` 拒绝也会出现在这里（§「策略隔离」）。

### 2.3 `fills.csv`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `fill_id` | `str` | 引擎分配 |
| `date` | `str` | 成交日 |
| `order_id` | `str` | 关联订单 |
| `symbol` | `str` | |
| `side` | `str` | `BUY` / `SELL` |
| `price` | `Decimal` | 成交价（市价单按当日 open） |
| `quantity` | `int` | 成交股数 |
| `amount` | `Decimal` | `price * quantity` |
| `commission` | `Decimal` | 佣金（含 min_commission 保底） |
| `stamp_tax` | `Decimal` | 仅 SELL；BUY 必须为 0 |
| `transfer_fee` | `Decimal` | 默认 0 |
| `net_amount` | `Decimal` | BUY 正成本 = amount + commission + transfer_fee；SELL 净回款 = amount - commission - stamp_tax - transfer_fee |

### 2.4 `positions.csv`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `date` | `str` | 快照日 |
| `symbol` | `str` | |
| `quantity` | `int` | 当日收盘后的余额 |
| `sellable_quantity` | `int` | **D+1 起始时**的可卖数 = 当日结转后数 |
| `avg_cost` | `Decimal` | 滚动加权平均成本 |
| `close_price` | `Decimal` | D 日收盘价（无有效 close → DATA_ERROR） |
| `market_value` | `Decimal` | `quantity * close_price` |

`sellable_quantity` 结转后口径见 [`docs/metrics.md`](metrics.md) §6。

### 2.5 `costs.csv`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `fill_id` | `str` | 关联成交 |
| `commission` | `Decimal` | |
| `stamp_tax` | `Decimal` | |
| `transfer_fee` | `Decimal` | |
| `total_cost` | `Decimal` | `commission + stamp_tax + transfer_fee` |

`summary.json` 内的 costs 总和应与 `costs.csv` `total_cost` 之和一致。

## 3. `summary.json`

```jsonc
{
    "config_snapshot": { ... },          // 解析后的 BacktestConfig
    "trading_days": ["20240102", ...],   // [start_date, end_date] 实际命中的交易日
    "adjustment_policy": "none",
    "data_version": {
        "source": "tushare",
        "data_root": "~/.hqdata",
        "calendar_first": "20100101",
        "calendar_last": "20260815"
    },
    "metrics": { ... },                  // PerformanceMetrics 序列化
    "factor_diagnostics": [ ... ],       // 见 docs/factor-diagnostics.md
    "notes": [ ... ]                     // 边界说明（样本不足 / None 字段等）
}
```

`config_snapshot` 字段固定（不包含 `rule_set` 等运行时对象），保证跨运行字节稳定。

## 4. PerformanceMetrics 字段

| 字段 | 类型 | 公式 / 来源 |
| --- | --- | --- |
| `total_return` | `Decimal` | `(final_equity / initial_cash) - 1` |
| `daily_volatility` | `Decimal \| None` | 样本标准差（ddof=1）；样本不足返回 `None` |
| `annualized_volatility` | `Decimal \| None` | `daily_volatility * sqrt(annual_trading_days)` |
| `annualized_return` | `Decimal \| None` | `(1 + total_return) ** (N / annual_trading_days) - 1`，N < 2 → `None` |
| `sharpe_ratio` | `Decimal \| None` | `(annualized_return - risk_free_rate) / annualized_volatility`，零波动 → `None` |
| `max_drawdown` | `Decimal` | `max(peak - current) / peak`（来自 `equity_curve.drawdown`） |
| `turnover` | `Decimal` | `(sum(BUY 成交额) + sum(SELL 成交额)) / 2 / initial_cash` |
| `trade_count` | `int` | `len(fills)` |
| `win_rate` | `Decimal \| None` | `SELL 且 fill.price > 当时平均成本` / `SELL 总数`，无 SELL → `None` |
| `risk_free_rate` | `Decimal` | 从 `MetricsConfig`，默认 0 |
| `annual_trading_days` | `int` | 从 `MetricsConfig`，默认 252 |

`notes` 数组包含边界说明，例如：

```text
[
    "daily_volatility: insufficient samples (< 2 daily returns) — returned None",
    "sharpe_ratio: zero volatility — returned None",
    "win_rate: no SELL fills — returned None"
]
```

## 5. 事件日志（`events.jsonl`）

每行一条 JSON 事件，常见形态：

```json
{"timestamp": "2026-08-25T03:11:42Z", "phase": "BEFORE_TRADING_START", "date": "20240102",
 "type": "ORDER_CREATED", "order_id": "...", "symbol": "600000.SH", "side": "BUY", "quantity": 100}
```

`type` 取值（不穷举）：`SESSION_START`, `ORDER_CREATED`, `ORDER_REJECTED`, `ORDER_FILLED`, `BACKTEST_ENDED`, `DATA_WARNING`, `DATA_ERROR`, `ORDER_CANCELLED`, `BAR_CLOSE`, `AFTER_TRADING_END`。

完整字段约定见契约 [`docs/design/mvp-contract.md`](design/mvp-contract.md) 规则 10。

## 6. 字节稳定性

- 输入 + 数据 + `data_root` + `python_version`（主要版本）+ `hqbacktest_version` 一致时，下列文件字节相同：
    - `events.jsonl`
    - 五个 CSV
    - `summary.json`（去掉 `rule_set`）
- 唯一允许不一致：`run_metadata.json.timestamp_utc`。
- 浮点 Decimal 量化统一到 `Decimal('0.000000000001')`，防止二进制浮点跨平台漂移。

## 7. 工具函数

```python
from hqbacktest import BacktestResult

result.save("results/run-1")                       # 写出全部
restored = BacktestResult.load("results/run-1")     # 从目录重建 BacktestResult
```

`load` 解析全部 CSV + JSON，重新构造 `equity_curve` / `orders_table` / `fills_table` / `positions_table` / `costs_table` / `PerformanceMetrics`。`run_metadata.json` 不参与业务重建（仅审计）。
