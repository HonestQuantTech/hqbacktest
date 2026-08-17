# hqbacktest - A股量化策略回测与交易模拟引擎

<p align="center">
	<img src="https://img.shields.io/badge/status-planning-orange"/>
	<img src="https://img.shields.io/badge/data-hqdata-blue"/>
</p>

## 项目状态

`hqbacktest` 当前处于**规划与启动阶段**：仓库现阶段提供设计说明和串行实施路线图，尚未包含可安装的 Python 包、稳定的公开 API 或可执行的命令行工具。

本文的“目标用法”“目标命令行”和功能表用于先固定未来产品契约，方便按路线图逐步实现；其中的示例在对应能力落地前**不能直接运行**。产品契约、术语、日事件顺序与不可变规则见 [`docs/design/mvp-contract.md`](docs/design/mvp-contract.md)；本文与该文档的术语和默认值保持一致，任何契约变更必须先更新该文档。实际开发顺序、每步验收条件和 AI 协作提示见 [TODO.md](TODO.md)。

## 定位

`hqbacktest` 是 HonestQuant 量化系统的**策略回测与交易模拟层**，面向 A 股日线策略：

- 对下：仅通过 `hqdata.api`（及包级同名导出）的公开函数读取交易日历、历史股票池、日线和复权因子；不导入 `hqdata.sources`，也不直接调用 Tushare、RiceQuant 或 AkShare SDK。
- 对中：提供严格的交易日事件时钟、数据可见性控制、订单生命周期、虚拟经纪商、持仓账本和交易规则。
- 对上：让策略只通过受控的 `Context` 和 `DataView` 读取数据、提交订单和查询组合，不接触数据源实现或修改内部账本。
- 对外：输出可复现的净值、订单、成交、持仓、费用和绩效指标，用于研究和模拟，不连接真实券商。

```text
策略 ──> Context / DataView ──> BacktestEngine ──> SimulatedBroker ──> Portfolio
									  │
									  └──> MarketDataPortal ──> hqdata.api ──> 数据源
```

## 计划支持的主要功能

| 功能 | 目标接口/产物 | 首版语义 | 当前状态 |
| --- | --- | --- | :---: |
| 交易日与历史股票池 | `MarketDataPortal` | 按回测日获取交易日和股票池，避免以今日股票列表产生幸存者偏差 | 计划中 |
| 日线数据可见性 | `DataView.history()` | 盘前最多看到前一交易日；当天收盘后才可读取当天日线 | 计划中 |
| 策略生命周期 | `BaseStrategy` | `initialize`、盘前、收盘、日终四个回调 | 计划中 |
| 下单与撤单 | `Context.order_*()` | 首版只支持市价委托，策略只能提交意图，不能直接改账户 | 计划中 |
| 虚拟撮合与账本 | `SimulatedBroker`、`Portfolio` | 盘前订单按当日开盘价撮合；收盘订单最早次日开盘成交 | 计划中 |
| A 股基础规则 | `TradingRuleSet` | 整手、T+1、停牌/无价拒绝、现货多头和可配置成本 | 计划中 |
| 公司行为扩展 | `CorporateActionProvider` | 先定义权威公司行为数据与会计规则；v0.1 不根据复权因子改写账本 | 计划中 |
| 结果与分析 | `BacktestResult` | 净值、订单、成交、持仓、成本及基础绩效指标 | 计划中 |
| 配置与命令行 | `hqbacktest run` | 通过配置文件执行，并保存可复现的运行目录 | 计划中 |

## 首个可用版本的范围

首版以正确、可验证和可复现为优先，而不是一次覆盖所有交易品种和交易细则。

### 已规划的支持范围

- **市场与频率：** 沪深普通股票的日线回测；标的使用 `600000.SH`、`000001.SZ` 这类统一代码。
- **账户：** 单个人民币现金账户、股票现货多头；不使用杠杆或保证金。
- **数据：** 每次回测固定使用一个 `hqdata` 数据源。需要日线时，首选 Tushare 或 RiceQuant；当前 `hqdata` 的 AkShare 适配器不提供稳定的日线能力，不能作为首版回测数据源。
- **时间：** 日期一律使用 `YYYYMMDD`。`before_trading_start(D)` 只能访问 D-1 及以前的数据，可在 D 开盘参与撮合；`on_bar(D)` 在 D 收盘后才看到 D 日线，所提交订单最早在 D+1 开盘处理。
- **成交：** 首版市价单按符合规则的开盘价全额成交；订单、拒绝、费用和成交都要保留可追溯记录。
- **复权：** 成交、现金账本和 v0.1 净值均使用未复权价格，且 `adjustment_policy` 固定为 `none`。同源复权因子可用于数据质量诊断，但不用于伪造现金分红、送配、配股或税务会计。
- **结果：** 每次运行应导出净值曲线、订单、成交、每日持仓、成本、配置和运行元数据。

### 首版不支持

- 实盘交易、券商连接、实时行情和自动下单；
- 分钟线、Tick、盘中撮合、成交量参与率、限价单和止损单；
- 融资融券、卖空、期货、期权、多账户、多币种和组合级保证金；
- 没有可靠证券状态数据支撑的 ST、新股首日、涨跌停全部细则、北交所和复杂停复牌规则；
- 仅根据复权因子推断精确的现金分红、送配、配股及税费；
- 在 `hqdata` 尚未提供相应指数 API 前，把基准收益率作为运行的必需输入。

## 安装

### 当前阶段

当前仓库尚无 `pyproject.toml` 和可导入的包。现在应先阅读并按 [TODO.md](TODO.md) 的任务 1、任务 2 建立工程骨架，而不是尝试安装或运行 `hqbacktest`。

```bash
git clone git@github.com:HonestQuantTech/hqbacktest.git
cd hqbacktest
cat TODO.md
```

### 目标开发环境（任务 2 完成后）

以下命令是完成软件包骨架后的目标用法，届时会以实际的 `pyproject.toml` 为准：

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装本地数据层及所需数据源支持（以 tushare 为例）
pip install -e "../hqdata[tushare]"

# 可编辑安装回测引擎与开发依赖
pip install -e ".[dev]"
```

## 配置数据源

数据源凭证由 `hqdata` 负责加载，`hqbacktest` 不保存也不输出凭证。以 Tushare 为例，可在运行环境中设置：

```bash
export TUSHARE_TOKEN=your_token
```

也可使用 `hqdata` 约定的 `.env` 配置方式。使用 RiceQuant 时，应按其 SDK 和 `hqdata` 的配置要求准备权限与凭证。任何真实 token、账户号和本地私密配置均不应提交到仓库或写入回测结果。

## 目标 Python 用法

> 以下是任务 6 至任务 10 完成后应提供的目标 API 形态，用于固定易用性和时间语义；当前不可执行。

```python
from hqbacktest import BacktestConfig, BacktestEngine, BaseStrategy


class MovingAverageStrategy(BaseStrategy):
	def initialize(self, context):
		context.set_universe(["600000.SH"])

	def on_bar(self, context, data):
		closes = data.history("600000.SH", field="close", bar_count=20)
		if len(closes) < 20:
			return

		if closes.iloc[-5:].mean() > closes.mean():
			context.order_target_percent("600000.SH", target=0.95)
		else:
			context.order_target("600000.SH", target_quantity=0)


config = BacktestConfig(
	start_date="20200102",
	end_date="20231229",
	initial_cash="1000000",
	source="tushare",
	adjustment_policy="none",
)

result = BacktestEngine(
	config=config,
	strategy=MovingAverageStrategy(),
).run()

print(result.metrics)
result.save("results/moving-average")
```

### 策略回调与订单时点

| 回调 | 能看到的数据 | 市价单最早成交时间 | 适合的工作 |
| --- | --- | --- | --- |
| `initialize(context)` | 无逐日行情 | 不可下单或按最终契约明确限制 | 设置固定参数和初始股票池 |
| `before_trading_start(context, data)` | 前一交易日及以前 | 当日开盘 | 根据已知历史生成开盘订单 |
| `on_bar(context, data)` | 当日收盘后包含当天日线 | 下一交易日开盘 | 计算收盘信号并提交次日订单 |
| `after_trading_end(context)` | 当日完成后的账户快照 | 不可下单 | 记录、检查和分析 |

这套顺序是避免未来函数的核心约束：策略不会在看到当天收盘价后又以当天开盘价成交。

## 目标配置参数

| 参数 | 示例 | 说明 |
| --- | --- | --- |
| `start_date` / `end_date` | `"20200102"` | 回测的包含式日期区间，格式为 `YYYYMMDD` |
| `initial_cash` | `"1000000"` | 初始人民币现金；账本层将转换为精确金额类型 |
| `source` | `"tushare"` | 本次运行唯一的数据源；不能在一次回测中混用数据源 |
| `adjustment_policy` | `"none"` | v0.1 唯一合法值；精确公司行为会计完成并验证前，不支持因子总回报调整 |
| `universe` | `["600000.SH"]` | 可由策略初始化或配置文件声明的目标股票池 |
| `cost_model` | 配置节 | 佣金、最低佣金、印花税和可选过户费；费率必须显式配置 |

## 目标命令行

> 以下命令在任务 12 完成后提供，当前不可执行。

```bash
hqbacktest run --config configs/moving_average.toml --output results/moving-average
```

一次目标运行目录将至少包含：

```text
results/moving-average/
├── normalized_config.toml   # 规范化后的回测配置
├── run_metadata.json        # 包、Python、策略、数据源和版本信息
├── events.jsonl             # 事件与告警日志
├── equity_curve.csv         # 每日净值、现金和市值
├── orders.csv               # 全部订单及状态
├── fills.csv                # 全部成交与费用
└── positions.csv            # 每日持仓和估值
```

## 计划输出与指标

| 产物 | 内容 |
| --- | --- |
| `equity_curve` | 日期、现金、持仓市值、总资产、日收益和回撤 |
| `orders` | 委托方向、数量、创建时间、状态、拒绝原因和关联策略事件 |
| `fills` | 成交日期、价格、数量、金额、费用和关联订单 |
| `positions` | 每日总持仓、可卖数量、均价和市值；v0.1 不写因子调整记录 |
| `metrics` | 累计收益、年化收益、波动率、夏普比率、最大回撤、换手率、交易次数和胜率 |

所有指标会在实现时记录公式、年化交易日数、风险自由利率和异常边界。指标不构成投资建议，也不应被解释为未来收益承诺。

## 测试与开发

任务 2 完成后，项目的目标验证入口为：

```bash
pytest tests/ -v
```

单元测试必须使用内存数据或 mock，不依赖网络和真实凭证。确需验证 Tushare 或 RiceQuant 适配的集成测试必须在凭证不存在时自动跳过。每一项公开 API、订单规则、时间语义和绩效公式都应有可手算的回归测试。

## 实施顺序

不要直接从策略或图表开始。建议严格按 [TODO.md](TODO.md) 的顺序推进：

1. 固化产品契约与模块边界（详见 [`docs/design/mvp-contract.md`](docs/design/mvp-contract.md)）；
2. 建立可安装、可测试的软件包；
3. 先完成领域模型、数据可见性和事件时钟；
4. 再接入策略、订单、经纪商、A 股规则和因子调整；
5. 最后完成结果、示例、CLI、CI 和发布准备。

每完成一步，都应更新 README 中对应能力的状态，确保“计划中”和“已经验证可用”的边界始终清晰。

## 免责声明

`hqbacktest` 面向研究、教育和历史模拟。回测结果依赖数据质量、交易规则、成本模型、公司行为处理和策略假设，不能代表真实可实现收益，也不构成任何投资、交易或风险管理建议。项目在明确实现实盘能力前不会连接券商或执行真实委托。
