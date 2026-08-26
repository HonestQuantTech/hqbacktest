# hqbacktest - A股量化策略回测与交易模拟引擎

<p align="center">
    <img src="https://img.shields.io/badge/status-v0.1.4-blue"/>
    <img src="https://img.shields.io/badge/hqdata-%3E%3D0.1.22-blue"/>
    <img src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue"/>
</p>

`hqbacktest` 是 HonestQuant 量化系统的**策略回测与交易模拟层**，面向 A 股日线策略。它给量化研究者一个**确定性的、可复现的、与实盘严格隔离**的回测沙盒：策略只通过受控的 `Context` / `DataView` 读写数据、提交订单和查询组合，不接触数据源实现或内部账本；引擎负责时钟、撮合、规则与成本、指标和可审计的结果导出。

## 定位

- **对下：** 通过 `hqdata.api` 的 `csv` source 读取 `hqdata` CLI 已落盘的 CSV 快照（默认 `~/.hqdata/{source}/`）；不调用任何数据源 SDK、也不在回测运行时访问网络。
- **对中：** 提供严格的交易日事件时钟、数据可见性控制、订单生命周期、虚拟经纪商、持仓账本和交易规则。
- **对上：** 让策略只通过 `Context` / `DataView` 读取数据、提交订单和查询组合，不接触数据源实现或修改内部账本。
- **对外：** 输出可复现的净值、订单、成交、持仓、费用和绩效指标，用于研究和模拟，不连接真实券商。

## 已实现的能力

| 功能 | 目标接口 / 产物 | 首版语义 |
| --- | --- | --- |
| 日频事件时钟 | `BacktestEngine` | 五阶段固定顺序 `SESSION_START → BEFORE_TRADING_START → OPEN_MATCH → BAR_CLOSE → AFTER_TRADING_END`；盘前 D-1、收盘 D 的可见性切换；事件日志记录日期与阶段 |
| 交易日与历史股票池 | `MarketDataPortal` | 按回测日获取交易日和股票池，避免以今日股票列表产生幸存者偏差；`.BJ` 默认过滤，`include_bj=True` 保留 |
| 日线数据可见性 | `DataView.history()` | 盘前最多看到前一交易日；当天收盘后才可读取当天日线；首日盘前哨兵 `visible_through="00000000"` 不抛异常 |
| 缺行 / 停牌 / 估值口径 | `get_bars` / `DataView.current_price` / 日终估值 | `get_bars` 允许逐日间隙；停牌持仓按 20 日回看最近收盘估值并写 `DATA_WARNING`；整日快照缺失 → `SnapshotFileMissingError`；`Bar.volume` 单位「手」 |
| 策略生命周期 | `BaseStrategy`、`Context` | `initialize` / `before_trading_start` / `on_bar` / `after_trading_end` 四回调；只读 `Context` 暴露 `cash` / `positions` / `universe` / `pending_orders` / `history` / `current_price` / `total_equity`；下单意图 `order` / `order_value` / `order_target` / `order_target_value` / `order_target_percent` / `cancel_order` |
| 下单与撤单 | `Context.order_*()` | 首版只支持市价委托，仅盘前与收盘回调可下单；订单创建 / 撤销写入事件日志 |
| 虚拟撮合与账本 | `SimulatedBroker`、`Portfolio` | 盘前订单按当日开盘价撮合；收盘订单最早次日开盘成交；同批 SELL 先于 BUY；回测结束时未成交订单 `BACKTEST_ENDED` 撤销 |
| A 股基础规则 | `TradingRuleSet`、`CostModel` | 买入整手（卖出允许零股）、T+1、停牌 / 无价拒绝、现货多头、显式费率（佣金 0.025% + 5 元保底、印花税 0.1% 卖出） |
| 公司行为扩展 | `CorporateActionProvider`、`AdjustmentPolicy` | v0.1 仅 `adjustment_policy="none"`；`CorporateActionProvider` 是设计草案；因子诊断接口存在但默认不启用自动诊断 |
| 端到端示例 | `examples/buy_and_hold.py`、`examples/moving_average.py` | 仅用公共 API + 7 天 `InMemoryDataPortal` 确定性数据；`tests/examples/` 覆盖买-持、均线、T+1、费用、净值与指标 |
| 结果与分析 | `BacktestResult` | 净值曲线、订单 / 成交 / 持仓 / 费用 CSV + `summary.json` + `events.jsonl`；`PerformanceMetrics` 含累计 / 年化 / 波动 / 夏普 / 最大回撤 / 换手 / 胜率 |
| 配置与命令行 | `hqbacktest run` | TOML 配置 + 校验 + 策略导入 + 独立输出目录；`run_metadata.json` 不含凭证 / 完整环境；本地绝对路径脱敏为相对 cwd 路径 |

> 「已实现」与「不做」的完整边界见 [`docs/design/mvp-contract.md`](docs/design/mvp-contract.md)。模块级细节见 [`docs/`](.) 下的专题文档。

## 支持的数据源

| 数据 | 来源 | 适用场景 |
| --- | --- | --- |
| 真实日线 | `hqdata` CLI 落盘的 CSV 快照（`tushare` / `ricequant`）通过 [`HqDataCsvPortal`](src/hqbacktest/data/hqdata_portal.py) 读取 | 任何需要真实行情的回测 |
| 内存 fixture | `InMemoryDataPortal` | 单元测试、示例、`tests/examples/` 端到端 fixture |

`HqDataCsvPortal` 在构造时把 snapshot 路径传给 `hqdata.init_source("csv", root=...)`，所有 CSV 解析由 `hqdata.sources.csv_source.CsvSource` 负责（列名校验、文件存在性、整日缺失抛 `SnapshotFileMissingError`）。具体列名、缺失语义见 [`hqdata` README §本地 CSV 数据源](https://github.com/HonestQuantTech/hqdata)。

`hqdata` 当前 `akshare` 适配器不稳定，按其官方说明**不**作为本项目首选数据源。需要日线请使用 `tushare` 或 `ricequant`；具体数据下载与落盘见 [`hqdata` README](https://github.com/HonestQuantTech/hqdata)。

## 首个可用版本的范围

### 已支持

- **市场与频率：** 沪深普通股票的日线回测；统一代码 `600000.SH` / `000001.SZ`。
- **账户：** 单个人民币现金账户、股票现货多头；不使用杠杆或保证金。
- **数据：** 每次回测固定使用一个 `hqdata` CSV 快照；`data_root` 默认 `~/.hqdata`，`source` 指向其下的数据源目录。
- **时间：** `YYYYMMDD` 8 位日期；`before_trading_start(D)` 看到 D-1 及以前，可在 D 开盘撮合；`on_bar(D)` 收盘后看到 D 日线，最早 D+1 开盘成交。
- **成交量单位：** `Bar.volume` = 手（1 手 = 100 股；与 `hqdata` `tushare` 适配器口径一致）。
- **成交：** 市价单按符合规则的开盘价全额成交；订单、拒绝、费用与成交全程留痕。
- **复权：** 成交、现金账本和 v0.1 净值使用未复权价格；`adjustment_policy="none"`。
- **结果：** 每次运行产出净值曲线、订单、成交、每日持仓、成本、配置和运行元数据。

### 明确不支持

- 实盘交易、券商连接、实时行情和自动下单。
- 分钟线、Tick、盘中撮合、成交量参与率、限价单和止损单。
- 融资融券、卖空、期货、期权、多账户、多币种和组合级保证金。
- 没有可靠证券状态数据支撑的 ST / 涨跌停 / 新股首日 / 北交所细则。
- 仅由复权因子推断精确的现金分红、送配、配股及税费。
- 在 `hqdata` 尚未提供指数日线前，把基准收益率作为运行的必需输入。

## 安装

```bash
git clone git@github.com:HonestQuantTech/hqbacktest.git
cd hqbacktest

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 装数据层（hqbacktest 只依赖 hqdata 的 csv source；具体数据源 tushare/ricequant 由 hqdata CLI 异步下载落盘）
pip install -e "../hqdata"

# 可编辑安装本项目 + 开发依赖
pip install -e ".[dev]"
```

`pyproject.toml` 声明的 Python 目标版本为 3.10 / 3.11 / 3.12。

## 配置数据源

`hqbacktest` 不接触任何数据源 token，回测配置通过 `data_root` + `source` 定位 `hqdata` 已落盘的 CSV。

| 写法 | 含义 |
| --- | --- |
| `data_root="~/.hqdata"`, `source="tushare"` | 使用 `~/.hqdata/tushare` |
| `data_root="/mnt/market-data"`, `source="ricequant"` | 使用 `/mnt/market-data/ricequant` |

`source` 接受名称或绝对路径，底层 CSV 布局由 `hqdata` CLI 在回测前写入；`hqbacktest` 既不下载数据，也不保存凭证。

## 使用

最小 Python 示例（公共 API，详见 `examples/`）：

```python
from decimal import Decimal
from hqbacktest import BacktestConfig, BacktestEngine, BaseStrategy
from hqbacktest.data import DataView


class MovingAverageStrategy(BaseStrategy):
    def initialize(self, context):
        context.set_universe(["600000.SH"])

    def on_bar(self, context, data):
        closes = data.history("600000.SH", field="close", bar_count=5)
        if len(closes) < 5:
            return
        avg = sum(closes) / len(closes)
        if closes[-1] > avg:
            context.order_target_percent("600000.SH", Decimal("0.95"))
        else:
            context.order_target("600000.SH", 0)


result = BacktestEngine(
    BacktestConfig(start_date="20240102", end_date="20240110", initial_cash="100000", source="tushare"),
    strategy=MovingAverageStrategy(),
).run()
result.save("results/moving-average")
```

命令行（推荐用于 CI 与可复现实验）：

```bash
hqbacktest run --config configs/moving_average.toml --output results/moving-average
```

## 示例

```bash
python examples/buy_and_hold.py
python examples/moving_average.py
```

两份示例都用 7 天确定性 `InMemoryDataPortal` 数据走通端到端流程，不访问网络、不需要任何凭证。`tests/examples/` 下有 10 项端到端回归测试，覆盖买-持、均线、T+1、费用、净值与指标。

## 测试

```bash
pytest tests/ -v
```

单元测试必须使用内存数据或 mock，不依赖网络和本地行情文件；确需在真实数据上验证 `tushare` / `ricequant` 适配的集成测试必须在 `~/.hqdata/{name}` 不存在或不可读时自动跳过。

## 文档导览

| 文档 | 内容 |
| --- | --- |
| [`docs/design/mvp-contract.md`](docs/design/mvp-contract.md) | v0.1 产品契约：术语、模块边界、日事件顺序、不可变规则、非目标 |
| [`docs/cli.md`](docs/cli.md) | `hqbacktest run` 详细配置 schema、输出目录、错误码、复现性 |
| [`docs/output.md`](docs/output.md) | `BacktestResult.save(dir)` 输出文件结构与 `PerformanceMetrics` 字段含义 |
| [`docs/strategy-api.md`](docs/strategy-api.md) | 策略回调与下单时点、`Context` / `DataView` 可见性矩阵 |
| [`docs/matching.md`](docs/matching.md) | 撮合顺序、整手 / 零股、费用量化、`realized_pnl` 口径 |
| [`docs/metrics.md`](docs/metrics.md) | 首日 P&L、波动率样本、幂运算桥接、metrics 输出约定 |
| [`docs/isolation.md`](docs/isolation.md) | `Order` 不可变、`DataView` 私有 portal、universe 生效、历史股票池 |
| [`docs/factor-diagnostics.md`](docs/factor-diagnostics.md) | 因子诊断接入、分红偏差显性化、CLI 警告 |
| [`docs/performance.md`](docs/performance.md) | 双层缓存、真实数据基准、性能冒烟测试 |

## 免责声明

`hqbacktest` 面向研究、教育和历史模拟。回测结果依赖数据质量、交易规则、成本模型、公司行为处理和策略假设，不能代表真实可实现收益，也不构成任何投资、交易或风险管理建议。项目在明确实现实盘能力前不会连接券商或执行真实委托。
