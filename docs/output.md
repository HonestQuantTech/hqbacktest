# BacktestResult 输出

> 适用版本：v0.1。本文档描述 `BacktestResult.save(dir)` 与 CLI 运行目录写出的实际文件和 schema。指标口径见 [metrics.md](metrics.md)，CLI 配置和退出码见 [cli.md](cli.md)。

## 1. 文件总览

通过 Python API 调用 `result.save(directory)` 时，目录包含：

```text
<directory>/
├── events.jsonl
├── equity_curve.csv
├── orders.csv
├── fills.csv
├── positions.csv
├── costs.csv
└── summary.json
```

通过 `hqbacktest run` 调用时，CLI 还会在同一目录写入：

```text
├── config.toml              # 原始输入配置（精确文本）
└── run_metadata.json        # 引擎、Python、平台、配置路径与 git 信息
```

所有金额、价格和比率以十进制字符串写入 CSV 或 JSON；读取结果时应使用 `Decimal`，而不是二进制浮点数。

## 2. CSV schema

以下列顺序由 [result.py](../src/hqbacktest/engine/result.py) 固定，并由测试锁定。空表仍写入完整表头。

### 2.1 `equity_curve.csv`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `date` | `str` (`YYYYMMDD`) | 回测交易日。 |
| `cash` | `Decimal` | 当日现金余额。 |
| `market_value` | `Decimal` | 持仓市值，按当日有效 close 或有告警的回退 close 估值。 |
| `total_equity` | `Decimal` | `cash + market_value`。 |
| `daily_return` | `Decimal` | 首日为 `total_equity / initial_cash - 1`；后续为相对前一日总资产的收益。 |
| `drawdown` | `Decimal` | 相对包含 `initial_cash` 的滚动峰值的回撤。 |

### 2.2 `orders.csv`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `order_id` | `str` | 引擎分配的订单标识。 |
| `symbol` | `str` | 股票代码，例如 `600000.SH`。 |
| `side` | `str` | `BUY` 或 `SELL`。 |
| `quantity` | `int` | 订单数量（股）；买入已按整手规则处理。 |
| `order_type` | `str` | v0.1 固定为 `MARKET`。 |
| `status` | `str` | `FILLED`、`REJECTED`、`CANCELLED` 等最终订单状态。 |
| `created_at` | `str` (`YYYYMMDD`) | 创建交易日。 |
| `created_session` | `str` | 创建阶段：`BEFORE_TRADING_START` 或 `BAR_CLOSE`。 |
| `filled_at` | `str` 或空 | 全部成交日；未成交时为空。 |
| `avg_fill_price` | `Decimal` 或空 | 全部成交后的平均成交价。 |
| `commission_total` | `Decimal` 或空 | 此订单全部成交的佣金总额。 |
| `reject_reason` | `str` 或空 | 拒绝或撤销原因，例如 `OUT_OF_UNIVERSE`、`BACKTEST_ENDED`。 |
| `reject_detail` | `str` 或空 | 可审计的详细原因。 |

`OUT_OF_UNIVERSE` 拒绝会写入本表，即使该订单从未进入 broker。

### 2.3 `fills.csv`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `fill_id` | `str` | 引擎分配的成交标识。 |
| `order_id` | `str` | 关联订单。 |
| `symbol` | `str` | 股票代码。 |
| `side` | `str` | `BUY` 或 `SELL`。 |
| `quantity` | `int` | 成交数量（股）。 |
| `price` | `Decimal` | 成交价；v0.1 市价单按撮合日开盘价成交。 |
| `amount` | `Decimal` | 有符号成交额：BUY 为正，SELL 为负。 |
| `commission` | `Decimal` | 佣金，已包含最低佣金规则。 |
| `stamp_tax` | `Decimal` | 印花税；BUY 恒为 `0`。 |
| `other_fee` | `Decimal` | 其他费用；默认费用模型用它记录过户费。 |
| `filled_at` | `str` (`YYYYMMDD`) | 成交交易日。 |
| `session` | `str` | v0.1 固定为 `OPEN_MATCH`。 |

净现金流不单列：BUY 为 `-(amount + commission + other_fee)`；SELL 为 `-amount - commission - stamp_tax - other_fee`。

### 2.4 `positions.csv`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `date` | `str` (`YYYYMMDD`) | 日终快照日。 |
| `symbol` | `str` | 股票代码。 |
| `quantity` | `int` | 日终持仓数量（股）。 |
| `sellable_quantity` | `int` | 日终结转后的可卖数量，即下一交易日起可卖数。 |
| `avg_cost` | `Decimal` | 滚动加权平均成本。 |
| `market_price` | `Decimal` | 当天用于估值的价格；停牌时可能是有 `DATA_WARNING` 的回退 close。 |
| `market_value` | `Decimal` | `quantity * market_price`。 |

仅对数量大于零的持仓写行。

### 2.5 `costs.csv`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `date` | `str` (`YYYYMMDD`) | 成交交易日。 |
| `fill_id` | `str` | 关联成交。 |
| `order_id` | `str` | 关联订单。 |
| `symbol` | `str` | 股票代码。 |
| `side` | `str` | `BUY` 或 `SELL`。 |
| `quantity` | `int` | 成交数量（股）。 |
| `gross` | `Decimal` | 未扣费的绝对成交额。 |
| `commission` | `Decimal` | 佣金。 |
| `stamp_tax` | `Decimal` | 印花税。 |
| `other_fee` | `Decimal` | 其他费用（默认是过户费）。 |
| `net` | `Decimal` | 有符号净现金流，口径同 `Fill.net_amount()`。 |

## 3. `summary.json`

`summary.json` 是结果的稳定汇总，包含：

```jsonc
{
  "adjustment_policy": "none",
  "config_snapshot": { /* BacktestConfig 的可序列化快照 */ },
  "data_version": {
    "source": "tushare",
    "as_of": "20260731",
    "schema_version": "v0.1"
  },
  "factor_diagnostics": [ /* FactorDiagnostic 列表 */ ],
  "metrics": { /* PerformanceMetrics 或 null */ },
  "trading_days": ["20240102", "..."]
}
```

`data_version` 的精确字段由 portal 提供；当前标准实现为 `source`、`as_of`、`schema_version`。`rule_set` 等带运行时对象标识的配置不会写入 `config_snapshot`，以保持结果可复现。

## 4. `run_metadata.json`

仅 CLI 运行会写入此文件。其关键字段包括：

| 字段 | 说明 |
| --- | --- |
| `hqbacktest_version` | 执行回测的 hqbacktest 版本。 |
| `python`、`platform`、`timestamp_utc` | 运行环境与时间。 |
| `config_*` | 输入配置中的日期、初始资金、source、策略模块和策略类。 |
| `data_root`、`adjustment_policy` | 数据位置与复权策略配置。 |
| `git_commit` | hqbacktest 自身仓库的短 commit；不可用时为 `null`。 |

`config_path`、`output_directory` 和 `config_output_directory` 均以运行时工作目录的相对路径写入，避免将用户绝对目录写进结果。

## 5. `events.jsonl`

每行是一个 `EngineEvent` JSON 对象，固定字段为：

```json
{
  "date": "20240102",
  "phase": "ORDER_FILLED",
  "order_id": "O20240102-000001",
  "fill_id": "F20240103-000001",
  "error": null,
  "detail": "fill@10.0000 qty=100 comm=5.00 stamp=0.00"
}
```

`phase` 同时表示日内阶段或审计事件类型，常见值包括 `SESSION_START`、`BEFORE_TRADING_START`、`OPEN_MATCH`、`BAR_CLOSE`、`AFTER_TRADING_END`、`ORDER_CREATED`、`ORDER_REJECTED`、`ORDER_FILLED`、`ORDER_CANCELLED`、`DATA_WARNING` 与 `DATA_ERROR`。

## 6. 指标与公司行为边界

`metrics` 包含总收益、年化收益、波动率、夏普、最大回撤、换手率、成交数量和胜率。各公式、样本不足行为与精度约定见 [metrics.md](metrics.md)。

v0.1 的 `adjustment_policy` 固定为 `none`：成交、账本和净值使用未复权价格。`factor_diagnostics` 非空表示持仓期间发现了因子异常或跳变；诊断不会改变现金、持仓或净值。跨除权日的长区间净值可能低估分红现金，不能直接解释为总回报，详见 [factor-diagnostics.md](factor-diagnostics.md)。

## 7. 可复现性与回读

输入、策略、数据快照、Python 主版本与 hqbacktest 版本一致时，下列文件应字节相同：

- `events.jsonl`
- `equity_curve.csv`
- `orders.csv`
- `fills.csv`
- `positions.csv`
- `costs.csv`
- `summary.json`

`run_metadata.json.timestamp_utc` 是预期的时间差异。可用以下 API 回读业务结果：

```python
from hqbacktest import BacktestResult

restored = BacktestResult.load("results/run-1")
```

`load()` 会重建事件、净值、订单、成交、持仓、成本、指标和因子诊断；`run_metadata.json` 仅用于审计，不参与业务重建。