# hqbacktest - A股量化策略回测与交易模拟引擎

<p align="center">
	<img src="https://img.shields.io/badge/status-v0.1.3-blue"/>
	<img src="https://img.shields.io/badge/data-hqdata-blue"/>
</p>

## 项目状态

`hqbacktest` 当前发布 **`v0.1.3`**：

- **v0.1.3 hotfix（任务 23）：** 波动率 / Sharpe 与 `max_drawdown` 对首日盈亏的可见性现在一致——`metrics.compute_metrics` 不再从 `total_equity` 重新推导日收益（旧的零种子会让首日真实收益被丢，2 日回测的 `daily_volatility` 误报 `None`），改为直接读 `EquityPoint.daily_return`；删除死代码 `_drawdown_series`（自任务 17 后无调用点）；新增手算回归测试（2 日 -9% / +5.5%，`daily_volatility` ≈ 0.10253）。
- **v0.1.2 hotfix（任务 22）：** `source` 现在支持绝对路径（拆为 `data_root` + 名称，相对路径仍被拒绝以避免 cwd 模糊）；`run_metadata.json` 的 `config_path` / `output_directory` / `config_output_directory` 写入相对 cwd 的相对路径（`os.path.relpath`），本地绝对路径不再泄露；`validate_yyyymmdd` 用 `datetime.strptime` 校验假日期（如 `20241399` / `20240230`），保留 `"00000000"` 首日哨兵；任务 15 性能夹具生成器改用 `datetime.timedelta` 迭代，修复了整数计数器 `d % 100 == 32` 仅能识别 31 天月的日 32 回滚、遗漏短月日期（`20240230`/`20240231`/`20240431`/`20240631`）而混入假日期的隐藏 bug；CLI 测试中 `test_console_script_runs_end_to_end` 改名 `test_python_m_runs_end_to_end` 并补一个真正测 console script 的同名测试；README「26 项 CLI 测试」改为「见 `tests/cli/`」；`pyproject.toml` 删除过时「no runtime deps yet」注释。任务 24 排入下个迭代。
- **v0.1.1 新增（任务 14–21）：** 数据层缺行/停牌/首日语义修复（任务 14）；按日文件缓存 + 按 symbol 累积序列 + bisect 切片，性能从「小时级」降到「秒级」（任务 15）；同批撮合 SELL→BUY 滚动现金 + SELL 零股可卖 + 钉死 T+1/realized_pnl/ROUND_HALF_EVEN/撮合顺序（任务 16）；首日 P&L 进入收益曲线 + running peak 含 `initial_cash` + 波动率样本不足返回 `None` + 恒等式 ∏(1+r)=1+total_return（任务 17）；`Order` 不可变 + `DataView.portal` 私有 + universe 生效（任务 18）；持仓期间因子跳变自动诊断 + CLI 汇总警告 + 账本零影响（任务 19）；console script 策略模块导入 + nan/inf/空窗口校验 + 输出目录防护 + `order_value` 接受 int/str + 文档一致性（任务 20）；`tests/integration/` 真实数据冒烟基线（任务 21）。
- **v0.1 已实现（任务 1–13）：** 产品契约、可安装的 Python 包、领域模型（订单、成交、持仓、账本、快照）、订单状态机、`AdjustmentPolicy` 枚举、`CorporateAction` 数据结构草案、`Decimal` 精度与 JSON 序列化；`MarketDataPortal`、`HqDataCsvPortal`（CSV 快照门户）、`InMemoryDataPortal`、`DataView`、内存缓存和无未来函数校验；日频事件时钟、`BacktestEngine`、五阶段调度（`SESSION_START → BEFORE_TRADING_START → OPEN_MATCH → BAR_CLOSE → AFTER_TRADING_END`）、按阶段的数据可见性切换和可追溯事件日志；`BaseStrategy` 生命周期与受控 `Context` API、下单意图；`SimulatedBroker`（`OPEN_MATCH` 阶段按当日 `bar.open` 全额成交市价单）；`TradingRuleSet`（`LongOnly` / `LotSize` / `NonTradingDay` / `InvalidPrice` / `InsufficientCash` / `T1Sellable` 六条默认规则）和 `CostModel`（默认 A 股费率：0.025% 佣金 + 5 元保底 + 0.1% 卖出印花税，0 过户费）；账本拒绝原因（`INSUFFICIENT_CASH` / `INSUFFICIENT_SHARES`）和 T+1 日终结算；末交易日 `BACKTEST_ENDED` 自动撤销。`BacktestConfig.adjustment_policy` 严格只接受 `"none"`。`BacktestResult` 含 `equity_curve` / `orders_table` / `fills_table` / `positions_table` / `costs_table` / `PerformanceMetrics` / `events.jsonl` / `data_version` / `factor_diagnostics`；`save(dir)` / `load(dir)` 导出 CSV+JSON 并可重建。`examples/buy_and_hold.py` 与 `examples/moving_average.py` 用公共 API 跑通端到端流程并有 7 天确定性 `InMemoryDataPortal` 数据 fixture。`hqbacktest run --config FILE --output DIR` 命令行（`hqbacktest/cli/` 包，TOML 配置 + 校验 + 策略导入 + 元数据 + 独立输出目录，绝不泄露凭证）。`.github/workflows/ci.yml` 覆盖 Python 3.10 / 3.11 / 3.12、`black`、`pytest`、`pytest-cov`、示例 smoke 与 CLI smoke；`python -m build` 产出 sdist + wheel；`CHANGELOG.md` 记录 v0.1 / v0.1.1 / v0.1.2 / v0.1.3。

> **⚠️ v0.1.1 仍未做：** 分红会计 / 涨跌停 / 新股 / 北交所 / 限价单 / 指数基准 / 多账户 / 分钟线 / 实盘对接 —— 见 [CHANGELOG.md](CHANGELOG.md) 与 [TODO.md](TODO.md) 「发布后再排期的增强项」。`adjustment_policy=none` 下跨除权日的净值仍**系统性低估**（少分红现金），任务 19 因子诊断会显式记录此类跳变，但长区间结果不可直接用于收益评估。

- **不在 v0.1.1 内（路线图）：** 限价 / 止损单、成交量参与率、部分成交；ST / 涨跌停 / 新股 / 北交所规则；融资融券 / 期货 / 期权 / 多账户；指数基准与归因；Notebook 与远程策略入口；JSON Schema 校验以外的策略注册中心；交互图表 / HTML 报告。

本文描述的用法、命令行和功能表与 [`docs/design/mvp-contract.md`](docs/design/mvp-contract.md) 一致；其中的示例已经可以按 §示例 章节运行。功能表区分「已实现」与「路线图 / 计划中」；任何契约变更必须先更新契约文档。开发顺序与 AI 协作提示见 [TODO.md](TODO.md)。

## 定位

`hqbacktest` 是 HonestQuant 量化系统的**策略回测与交易模拟层**，面向 A 股日线策略：

- 对下：只读 `hqdata` CLI 已落盘的 CSV 快照，默认根目录为 `~/.hqdata`；不导入 `hqdata`、不直接调用 Tushare、RiceQuant 或 AkShare SDK，也不在回测运行时访问网络。
- 对中：提供严格的交易日事件时钟、数据可见性控制、订单生命周期、虚拟经纪商、持仓账本和交易规则。
- 对上：让策略只通过受控的 `Context` 和 `DataView` 读取数据、提交订单和查询组合，不接触数据源实现或修改内部账本。
- 对外：输出可复现的净值、订单、成交、持仓、费用和绩效指标，用于研究和模拟，不连接真实券商。

```text
策略 ──> Context / DataView ──> BacktestEngine ──> SimulatedBroker ──> Portfolio
									  │
									  └──> MarketDataPortal ──> hqdata CSV snapshot
```

## 计划支持的主要功能

| 功能 | 目标接口/产物 | 首版语义 | 当前状态 |
| --- | --- | --- | :---: |
| 交易日与历史股票池 | `MarketDataPortal` | 按回测日获取交易日和股票池，避免以今日股票列表产生幸存者偏差；`.BJ` 股票默认过滤，`include_bj=True` 可保留 | 已实现 |
| 日线数据可见性 | `DataView.history()` | 盘前最多看到前一交易日；当天收盘后才可读取当天日线；首日盘前哨兵 `visible_through="00000000"` 不抛异常 | 已实现 |
| 缺行/停牌/估值口径 | `get_bars`/`DataView.current_price`/日终估值 | `get_bars` 允许逐日间隙；停牌持仓按 20 日回看最近收盘估值并写 `DATA_WARNING`；整日快照缺失抛 `SnapshotFileMissingError`；`Bar.volume` 单位「手」 | 已实现 |
| 日频事件时钟 | `BacktestEngine`、`EventLog` | 五阶段固定顺序；盘前 D-1、收盘 D 的可见性切换；事件日志记录日期、阶段与错误原因；策略异常带日期和阶段 | 已实现 |
| 策略生命周期 | `BaseStrategy`、`Context` | `initialize`/`before_trading_start`/`on_bar`/`after_trading_end` 四个回调；只读 `Context` 暴露 `cash` / `positions` / `universe` / `pending_orders` / `history` / `current_price` / `total_equity`；下单意图（`order`/`order_value`/`order_target`/`order_target_value`/`order_target_percent`/`cancel_order`）；市价单且与数据可见性 / 账本严格隔离 | 已实现 |
| 下单与撤单 | `Context.order_*()` | 首版只支持市价委托，策略只能提交意图，不能直接改账户；仅盘前与收盘回调可下单，订单创建/撤销写入事件日志 | 已实现 |
| 虚拟撮合与账本 | `SimulatedBroker`、`Portfolio` | 盘前订单按当日开盘价撮合；收盘订单最早次日开盘成交；回测结束时未成交订单以 `BACKTEST_ENDED` 撤销 | 已实现 |
| A 股基础规则 | `TradingRuleSet`、`CostModel` | 买入整手（卖出允许零股）、T+1、停牌/无价拒绝、现货多头与显式 A 股费率（佣金 0.025% + 5 元保底；卖出印花税 0.1%）；所有费率在配置与 README 中显式声明 | 已实现 |
| 公司行为扩展 | `CorporateActionProvider`、`AdjustmentPolicy`、`analyze_factor_series` | v0.1 仅 `adjustment_policy="none"`；`CorporateActionProvider` 为设计草案（10 个权威字段）；`factor_total_return` 准入标准（7 项会计语义）已条目化；因子诊断分析器可对缺失/零负/异常跳变/跨源不一致生成可追溯诊断，引擎默认不自动启用；任何其他 `adjustment_policy` 在配置校验时拒绝 | 部分实现 |
| 端到端示例 | `examples/buy_and_hold.py`、`examples/moving_average.py` | 仅用公共 `BaseStrategy` + `Context` API；7 天 `InMemoryDataPortal` 确定性数据；10 项端到端回归测试覆盖买入、下单、次日成交、T+1、费用、净值与指标导出 | 已实现 |
| 结果与分析 | `BacktestResult`、`PerformanceMetrics` | 净值曲线、订单/成交/持仓/费用 CSV + `summary.json` + `events.jsonl`；累计收益 / 年化 / 日与年化波动率 / 夏普 / 最大回撤 / 换手率 / 交易次数 / 胜率 / 边界 notes；持仓无价即运行失败（`DATA_ERROR`)；公式在 `engine/metrics.py` 注释中显式 | 已实现 |
| 配置与命令行 | `hqbacktest run`、`cli/config.py`、`cli/runner.py` | TOML 配置 + 校验 + 策略导入 + 独立输出目录（`config.toml` / `run_metadata.json` / `events.jsonl` / 五个 CSV + `summary.json`）；`rule_set` 从快照中剥离以保 summary.json 字节稳定；CLI 测试覆盖端到端、配置验证、可复现性与错误信息（见 `tests/cli/`）；`run_metadata.json` 不写入 token / 完整环境变量；本地绝对路径在写入时脱敏为相对 cwd 的相对路径（任务 22.2） | 已实现 |

## 首个可用版本的范围

首版以正确、可验证和可复现为优先，而不是一次覆盖所有交易品种和交易细则。

### 已规划的支持范围

- **市场与频率：** 沪深普通股票的日线回测；标的使用 `600000.SH`、`000001.SZ` 这类统一代码。`.BJ`（北交所）股票默认从 `get_universe` 中过滤，需要时通过 `include_bj=True` 显式启用。
- **账户：** 单个人民币现金账户、股票现货多头；不使用杠杆或保证金。
- **数据：** 每次回测固定使用一个 `hqdata` 数据源。需要日线时，首选 Tushare 或 RiceQuant；当前 `hqdata` 的 AkShare 适配器不提供稳定的日线能力，不能作为首版回测数据源。
- **时间：** 日期一律使用 `YYYYMMDD`。`before_trading_start(D)` 只能访问 D-1 及以前的数据，可在 D 开盘参与撮合；`on_bar(D)` 在 D 收盘后才看到 D 日线，所提交订单最早在 D+1 开盘处理。
- **缺行与停牌（任务 14）：** `get_bars` 允许逐日间隙（窗口内无任何行返回 `[]`）；个股当日缺行（停牌 / 未上市 / 已退市）属正常业务结果；**整日快照文件缺失** 是基础设施错误，必须中止运行。停牌持仓估值采用「最近 20 个交易日内最近一个有效收盘价」并写入 `DATA_WARNING` 事件；首日盘前哨兵 `visible_through="00000000"` 不抛异常。
- **成交量单位：** `Bar.volume` 单位为「**手**」（1 手 = 100 股；与 Tushare `hqdata` 适配器口径一致）；需要股数时应乘以 `LOT_SIZE`。
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

`hqbacktest` v0.1 已完成可安装包、领域模型、受限 CSV 数据层、日频事件时钟、受控策略接口、开盘市价撮合、可替换的 A 股规则与成本模型、公司行为扩展的设计门槛、可审计的结果对象与绩效指标、端到端示例与回归基准，以及 TOML 配置 + `hqbacktest run` CLI（任务 2–12）：买入并持有与均线策略在 7 天 `InMemoryDataPortal` 上产出确定的现金、持仓、成交、费用明细与净值曲线，规则触发的拒绝原因会出现在事件日志中，`adjustment_policy` 在配置校验层被严格收敛到 `"none"`，`BacktestResult` 记录策略、因子诊断、五个 CSV 表与 `PerformanceMetrics`，`save(dir)` / `load(dir)` 可重建，CLI 输出目录在两次相同输入下字节相同（去除 runtime 内存地址后）。CI（任务 13）覆盖 Python 3.10/3.11/3.12、black、`pytest`、`pytest-cov`、示例脚本与 CLI smoke，并产出可安装的 `sdist` + `wheel`。

```bash
git clone git@github.com:HonestQuantTech/hqbacktest.git
cd hqbacktest
```

### 开发环境

本仓库使用 Python `>=3.10`，与 `hqdata` 的 Python 下限保持一致。`pyproject.toml` 同时声明 `3.10`、`3.11`、`3.12` 作为目标版本。

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装本地数据层及所需数据源支持（以 tushare 为例）
pip install -e "../hqdata[tushare]"

# 可编辑安装回测引擎与开发依赖
pip install -e ".[dev]"
```

### 包布局

```text
hqbacktest/
├── pyproject.toml          # 构建、依赖、pytest 与 black 配置
├── src/hqbacktest/         # 引擎源码（src 布局）
│   ├── __init__.py         # 版本及稳定的公开 API 导出
│   ├── __main__.py         # `hqbacktest run` CLI 入口（任务 12）
│   ├── domain/             # 任务 3 的模型、状态机、精度与序列化
│   ├── data/               # 任务 4 的数据门户、DataView、缓存与校验
│   ├── engine/             # 任务 5–6 的事件时钟、调度器、BacktestEngine 与受控策略接口
│   └── cli/                # 任务 12 的 TOML 配置解析、CLI runner、退出码
├── tests/                  # 单元测试，必须不依赖网络或本地行情文件
├── examples/               # 端到端示例（任务 11：buy_and_hold / moving_average）
└── docs/design/            # 设计文档（如 mvp-contract.md）
```

## 配置数据源

`hqbacktest` 只读 hqdata CLI 已落盘的本地 CSV，不接触任何数据源 token，也不在回测运行时调用 `hqdata.api`。数据下载和更新须在回测前由 `hqdata` CLI 完成。

回测配置使用 `data_root` 和 `source` 来定位已落盘数据。`data_root` 默认为 `~/.hqdata`，`source` 是其下的数据源目录名：

| 写法 | 含义 |
| --- | --- |
| `data_root="~/.hqdata"`, `source="tushare"` | 使用 `~/.hqdata/tushare` |
| `data_root="/mnt/market-data"`, `source="ricequant"` | 使用 `/mnt/market-data/ricequant` |

数据集必须包含 `calendar.csv`，以及按交易日组织的 `stock_list/{YYYYMMDD}.csv`、`stock_daily/{YYYYMMDD}.csv` 与 `stock_factor/{YYYYMMDD}.csv`。这些文件由 `hqdata` CLI 在回测前写入；hqbacktest 既不下载数据，也不保存凭证。任何真实 token、账户号或私密配置都**不应**提交到仓库，也不应出现在回测结果目录中。

### 性能与内存（任务 15）

`HqDataCsvPortal` 在单次回测中按「按日文件缓存 + 按 symbol 累积序列」两层缓存：

- 每个 `stock_daily/{D}.csv` / `stock_factor/{D}.csv` 在一次运行中最多解析一次；解析结果以 `{date: {symbol: Bar}}` 形式缓存。
- 每个 symbol 在内存中维护一个按日期升序的累积序列；`get_bars` / `get_factor` 在该序列上做 `bisect` 切片，单次调用 O(log N)。
- `DataView.history` 走累积缓存，单次 `get_bars` 切片即可；`DataView.current_price` 仅需一次 `get_calendar`（有缓存）确定 20 个交易日回看起点 + 一次 `get_bars`，二者都避免了旧实现的逐日 `get_bars(day, day)` 往返。
- `Bar` / 因子对象在重叠窗口间复用，仅返回列表的防御性拷贝。

真实数据基准（`~/.hqdata/tushare`，20260105–20260731，139 个交易日，每个 daily 文件约 5000 行）：

| 场景 | 总耗时（含首次数据加载） | 任务 15 目标 |
| --- | --- | --- |
| 5 stocks × 139 days MA 策略 | ~7.6 s | < 10 s ✅ |
| 300 stocks × 139 days MA 策略 | ~9.4 s | < 120 s ✅ |

内存量级：每个 `Bar` 约 200 字节。覆盖完整窗口（5000 symbols × 139 days ≈ 70 万 Bar）约 140 MB；策略触及的 universe 通常远小于全市场。`_symbol_bars` 累积只对真实访问过的 symbol 增长。

性能冒烟测试在 `tests/data/test_task15_performance.py`，50 symbols × 250 days 全量 `history(bar_count=20)` 在 15 秒阈值内完成。

### 撮合与账本口径（任务 16）

- **同日撮合顺序：** 单个 `OPEN_MATCH(today)` batch 内**所有 SELL 先撮合、再撮合 BUY**（A 股「卖出资金当日可用」），同侧内保持策略提交顺序。资金检查用**滚动现金**而非撮合前快照。
- **整手取整：** 仅 BUY 按 100 股整手向下取整；SELL 允许任意正整数股（含零股），静默截到整手违反契约。`order_target(symbol, 0)` 必须能清仓含零股的持仓。
- **T+1 可卖不足：** 整单拒绝（`INSUFFICIENT_SHARES`），不支持部分截断。
- **`realized_pnl` 不含费用**：仅 `(sell_price - avg_cost) × quantity`；费用（commission / stamp_tax / other_fee）只走现金账。
- **金额量化：** 全部 `ROUND_HALF_EVEN`（`quantize_cash` 0.01 元 / `quantize_price` 0.0001 元），与券商「四舍五入到分」存在 1 分级差异。
- **`Fill.BUY` 不携带 `stamp_tax`：** 印花税仅在 SELL 收取；`Fill.__post_init__` 拒绝 BUY 携带非零 stamp_tax 以保证账本与 costs 表一致。
- **`intents.target_quantity_for_value(0)` 返回 0**（flatten），与 docstring 一致。
- **CLI `initial_cash` 拒绝 float**（TOML 字面 `100000.0` 报错），与 `BacktestConfig` 严格度对齐（contract rule 5）。

完整口径见 `docs/design/mvp-contract.md` §3.4。手算回归测试在 `tests/engine/test_task16_matching.py`。

### 净值与绩效指标口径（任务 17）

- **首日 P&L 进入曲线：** `daily_return[0] = total_equity[0] / initial_cash - 1`、`drawdown[0] = (initial_cash - total_equity[0]) / initial_cash`，不再硬编码 0；**后续日回撤 running peak = `max(initial_cash, 历史 total_equity)`**（峰值序列以 `initial_cash` 为初始峰值）；满足恒等式 `∏(1 + daily_return) = 1 + total_return`（Decimal 精度内）。
- **波动率样本不足返回 `None`：** `< 2` 个日收益时 `daily_volatility` / `annualized_volatility` / `sharpe_ratio` 返回 `None` 并附 note，禁止错报 0。真正 0 波动率才返回 `Decimal('0')`。
- **幂运算桥接：** `(1 + total_return) ** (n / 252)` 通过 `Decimal(str(float(...)))` 重建为 Decimal，避免 `Decimal(float(...))` 直接继承二进制浮点。
- **Decimal 量化：** 所有 `float` 桥接的 Decimal 输出统一 quantize 到 `Decimal('0.000000000001')`，保证 `summary.json` 干净。
- **`positions.sellable_quantity` 口径：** **结转后**（D 行快照显示 D+1 起始时可卖数）。engine 在 `_snapshot_equity` 前调用 `settle_t1`，D 行的 `sellable_quantity` 已包含当日成交的滚动。

完整口径见 `docs/design/mvp-contract.md` §3.5。手算回归测试在 `tests/engine/test_task17_metrics.py`。

### 策略隔离与审计完整性（任务 18）

- **`Order` 不可变：** `@dataclass(frozen=True)`，策略通过 `Context.pending_orders()` 拿到 Order 后无法修改任何字段（`quantity` / `avg_fill_price` / `fill_ids` 等）。`transition` / `record_fill` 用 `object.__setattr__` 写入冻结字段，仅 engine / broker 可调用。
- **`DataView.portal` 私有：** 字段名 `_portal`，策略无法通过 `view.portal.get_bars(sym, future_date)` 读到 `visible_through` 之后的数据；所有数据访问走 `view.history` / `view.current_price` / `view.universe`。
- **Universe 生效：** `set_universe([...])` 后对未声明符号下单立即拒绝（`RejectReason.OUT_OF_UNIVERSE`，含 ORDER_CREATED + ORDER_REJECTED 事件，Order 不经过 broker、停在 `_out_of_universe_orders` 并在 result 构造时折入 `orders_table`）；未设 universe 时不限制。
- **历史股票池：** `Context.historical_universe()` 返回 `visible_through` 当日的 portal 股票池（默认排除 `.BJ`），受可见性约束，不暴露 raw portal。
- **返回值防御性：** `pending_orders()` / `universe()` / `historical_universe()` 均返回 list 副本；Bar / Factor 跨查询复用（任务 15）。

完整口径见 `docs/design/mvp-contract.md` §3.6。回归测试在 `tests/engine/test_task18_isolation.py`。

### 因子诊断与分红偏差显性化（任务 19）

> ⚠️ **`adjustment_policy=none` 下，跨除权日的净值系统性低估（少分红现金）。**

`v0.1` 严格不实现分红会计（契约任务 9）。任务 19 让偏差**可见**：

- 引擎对**当前持仓**标的的因子跳变（|Δfactor/factor| > **0.1%**）自动生成 `DATA_WARNING` 事件、`FactorDiagnostic` 记录；写入 `result.factor_diagnostics`、`summary.json`、`events.jsonl`。清仓后（持仓归零）停止告警，持有期结束。
- **账本零影响**：诊断是只读观测，cash / position / equity 与无诊断时 byte-identical（`test_diagnostics_do_not_change_ledger` 锁定）。
- CLI 末尾打印一行汇总：`warning: N corporate-action factor jumps detected during holding periods; NAV excludes dividends (adjustment_policy=none), see summary.json`。
- 跨除权日长区间结果不可用于收益评估，必须先评估 factor 跳变并改用 `factor_total_return`（v0.1.1+ 后续任务）；README 显著位置保留此声明并链接诊断输出。

完整口径见 `docs/design/mvp-contract.md` §3.7。复刻 600000.SH 2026-07-16 除权案例的回归测试在 `tests/engine/test_task19_factor_diagnostics.py`。

## Python 用法（已实现）

> 下面的代码就是 `examples/moving_average.py` 的简化版，演示了 `Context` 与 `DataView` 的基本交互。**端到端运行请用 CLI（见 §命令行）或直接 `python -m hqbacktest run`**；完整示例见 `examples/`。

```python
from decimal import Decimal

from hqbacktest import BacktestConfig, BacktestEngine, BaseStrategy, EventType
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


config = BacktestConfig(
	start_date="20240102",
	end_date="20240110",
	initial_cash="100000",
	source="tushare",
)

result = BacktestEngine(config, strategy=MovingAverageStrategy()).run()

print("total_return", result.metrics.total_return)
result.save("results/moving-average")
```

### 策略回调与订单时点

| 回调 | 能看到的数据 | 市价单最早成交时间 | 适合的工作 |
| --- | --- | --- | --- |
| `initialize(context)` | 无逐日行情（引擎强制，读取即报错） | 不可下单（引擎强制） | 设置固定参数和初始股票池 |
| `before_trading_start(context, data)` | 前一交易日及以前 | 当日开盘 | 根据已知历史生成开盘订单 |
| `on_bar(context, data)` | 当日收盘后包含当天日线 | 下一交易日开盘 | 计算收盘信号并提交次日订单 |
| `after_trading_end(context)` | 当日完成后的账户快照 | 不可下单 | 记录、检查和分析 |

这套顺序是避免未来函数的核心约束：策略不会在看到当天收盘价后又以当天开盘价成交。

## 配置参数

| 参数 | 示例 | 说明 |
| --- | --- | --- |
| `start_date` / `end_date` | `"20240102"` | 回测的包含式日期区间，格式为 `YYYYMMDD` |
| `initial_cash` | `"100000"` | 初始人民币现金；账本层将其作为 `Decimal` 字段使用 |
| `data_root` | `"~/.hqdata"` | hqdata CSV 的根目录；可使用绝对路径覆盖 |
| `source` | `"tushare"` | 本次运行唯一的数据源目录名；裸名（如 `"tushare"`）解析为 `{data_root}/{source}`，绝对路径（如 `~/.hqdata/tushare`）拆为 `(parent_dir, basename)` 后定位快照（任务 22.1）；不能在一次回测中混用 |
| `adjustment_policy` | `"none"` | v0.1 唯一合法值；精确公司行为会计完成并验证前，不支持因子总回报调整 |
| `universe` | `["600000.SH"]` | 可由策略在 `initialize` 中声明的目标股票池 |
| `cost_model` | 配置节 | 佣金、最低佣金、印花税和可选过户费；费率必须显式配置 |

> 这张表是 v0.1 已实现的 `BacktestConfig` 字段。CLI 配置 TOML 用同名 key；CLI 还会接受 `--output` 覆盖 `[output].directory`。

## 命令行（任务 12 已实现）

`hqbacktest run` 命令读取一份 TOML 配置，运行回测，将结果写入独立目录。退出码非零时唯一一行错误输出到 stderr。

### 安装 / 入口

`hqbacktest` 通过 `pyproject.toml` 的 `[project.scripts]` 注册为 console script。可编辑安装后可直接使用：

```bash
hqbacktest run --config configs/moving_average.toml --output results/moving-average
# 或：
python -m hqbacktest run --config configs/moving_average.toml --output results/moving-average
```

**策略模块解析（任务 20）：** `hqbacktest run` 会把 config 文件所在目录和当前工作目录加入 `sys.path`，让 `[strategy].module = "my_strategy"` 这类不带点号的写法能直接 import 成功（与 `python -m hqbacktest run` 行为一致）。

`--output` 可选：省略时使用配置中 `[output].directory`；提供时覆盖该值（例如 CI 里把结果重定向到临时目录）。

### 配置 schema

```toml
[start]
start_date = "20240102"        # YYYYMMDD，必填
end_date   = "20240105"        # YYYYMMDD，必填

[capital]
initial_cash = "100000"        # Decimal 字符串，必填，>= 0

[data]
source = "tushare"             # 数据源名称或绝对路径，必填
data_root = "~/.hqdata"        # 可选；默认 ~/.hqdata，指向 CSV 快照根目录

[strategy]
module = "examples.buy_and_hold"   # 可导入的 Python 模块，必填
class_name = "BuyAndHold"          # 可选；省略时使用模块内第一个 BaseStrategy 子类
[strategy.kwargs]                 # 可选：传给策略构造函数的参数表
answer = 42

[cost_model]                          # 可选：不填则使用 v0.1 默认费率
commission_rate   = "0.00025"        # 0.025%
min_commission    = "5.00"           # 5 元保底
stamp_tax_rate    = "0.001"          # 0.1%（仅 SELL）
transfer_fee_rate = "0.0"            # 过户费（v0.1 默认 0）

[output]
directory = "results/run-1"          # 输出目录，必填；不存在则创建；可被 CLI --output 覆盖
```

任何未知 section 或 key 都会触发 `ConfigError` 并产生非零退出码，便于 CI 即时报错。

### 输出目录

每次运行在 `directory` 下生成以下文件（互不重叠、可独立读出）：

```text
results/run-1/
├── config.toml              # 你传入的原始 TOML（精确字节）
├── run_metadata.json        # hqbacktest / Python / 平台 / 时间戳 / git commit
├── events.jsonl             # 引擎事件日志（每行一条 JSON）
├── equity_curve.csv         # 日期、现金、持仓市值、总资产、日收益、回撤
├── orders.csv               # 全部订单及状态
├── fills.csv                # 全部成交与费用
├── positions.csv            # 每日持仓和估值
├── costs.csv                # 每次成交的费用拆分
└── summary.json             # 配置快照 + 指标 + 因子诊断
```

`summary.json` 跨运行字节级稳定（去掉 `rule_set` 等含内存地址的运行时对象）；同输入同数据 → 同输出，可用 `diff` 复核差异。

### 错误信息

| 情况 | 退出码 | 错误示例 |
| --- | --- | --- |
| 配置文件缺失 / 不可读 | 2 | `config file not found: configs/missing.toml` |
| TOML 语法错 | 2 | `config file configs/bad.toml is not valid TOML: ...` |
| 必填字段缺失 | 2 | `[start] missing required key 'start_date'` |
| 未知 section / key | 2 | `unknown config sections: ['extra']; allowed: [...]` |
| 日期格式错 | 2 | `[start].start_date: not a valid calendar date: '20241399' (...)` 或 `must be 8 digits, got '2024-01-02'` |
| `initial_cash = nan` / `inf` | 2 | `[capital].initial_cash=NaN must be a finite number ...` |
| `initial_cash = float` | 2 | `[capital].initial_cash must be int/str/Decimal; float is forbidden ...` |
| 空交易窗口 | 2 | `no trading days in [...] for source 'memory'; ...` |
| 策略模块无法导入 | 2 | `could not import strategy module 'examples.foo': ...` |
| 策略类非 BaseStrategy 子类 | 2 | `MyStrategy is not a BaseStrategy subclass` |
| 策略无 class_name 且模块无 BaseStrategy | 2 | `no BaseStrategy subclass found in ...` |
| 输出目录不可创建 / 不可写 | 3 | `cannot create output directory ...: ...` |
| 输出目录已含旧结果文件 | 3 | `output directory ... already contains prior-run files; pass force=True ...` |
| 引擎异常（非 RunFailed） | 4 | `backtest run failed: ...` |
| 成功 | 0 | stdout: `hqbacktest: wrote results to results/run-1` |

CLI 同时支持 `--force` 覆盖已有输出目录：`hqbacktest run --config FILE --output DIR --force`。

### 复现性

两次相同输入 + 相同数据 + 相同 `data_root` 的运行：

- `events.jsonl` 字节相同
- `equity_curve.csv` / `orders.csv` / `fills.csv` / `positions.csv` / `costs.csv` 字节相同
- `summary.json` 字段值相同（去除内存地址等运行时常量后）
- 唯一的非确定性字段是 `run_metadata.json` 的 `timestamp_utc`（记录运行时刻，非结果数据）

## 输出与指标

`BacktestResult.save(dir)` 写出以下文件（任务10实现，公式在 `src/hqbacktest/engine/metrics.py` 顶部注释中显式）：

| 文件 | 内容 |
| --- | --- |
| `equity_curve.csv` | 日期、现金、持仓市值、总资产、日收益、回撤 |
| `orders.csv` | 委托方向、数量、订单类型、状态、关联成交与拒绝原因 |
| `fills.csv` | 成交日期、价格、数量、金额、佣金、印花税、关联订单 |
| `positions.csv` | 每日每持仓标的的余额、可卖量、均价、当日收盘价、市值 |
| `costs.csv` | 每次成交的费用拆分（佣金、印花税、过户费、净额） |
| `events.jsonl` | 完整事件日志（每行一个 JSON 事件，含订单/成交 ID 与错误原因） |
| `summary.json` | 配置快照、交易日、调整策略、数据来源（`data_version`）、因子诊断、指标 |

指标公式（手算见 `tests/engine/test_metrics.py` 与 `tests/engine/test_task17_metrics.py`）：

- 累计收益 `(final_equity / initial_cash) - 1`
- 日收益 `EquityPoint.daily_return`（引擎写入，首日锚定到 `initial_cash`——见任务 23）
- 年化收益 `(1 + total_return) ** (N / annual_trading_days) - 1`，N < 2 时为 `None`
- 日波动率 `stdev(equity_curve.daily_returns)`（样本标准差，ddof=1），序列 < 2 时为 `None`
- 年化波动率 `daily_volatility * sqrt(annual_trading_days)`
- 夏普比率 `(annualized_return - risk_free_rate) / annualized_volatility`，零波动时为 `None`
- 最大回撤 `max(peak - current) / peak`（从净值曲线取）
- 换手率 `(sum(BUY 成交额) + sum(SELL 成交额)) / 2 / initial_cash`（单边均值）
- 交易次数 `len(fills)`
- 胜率 `SELL 且 fill.price > 当时平均成本` / `SELL 总数`（按时间顺序单遍重放），无 SELL 时为 `None`

`risk_free_rate`（年化）与 `annual_trading_days`（默认 252）走 `MetricsConfig`，可追溯到配置；零分母与样本不足会在 `notes` 字段里显式说明。持仓标的在某日无有效收盘价时运行失败并记录 `DATA_ERROR` 事件（契约 §4：不使用前收、插值或静默按零估值）。指标不构成投资建议，也不应被解释为未来收益承诺。

## 示例

端到端示例用 7 天确定性 `InMemoryDataPortal` 数据展示完整的下单-撮合-导出流程，不访问网络、不需要任何凭证：

```bash
python examples/buy_and_hold.py
python examples/moving_average.py
```

两份示例都只使用公开 `BaseStrategy` + `Context` API（`set_universe` / `order` / `order_target` / `order_target_percent` / `history` / `current_price`），从不接触引擎或账本的私有字段。`tests/examples/` 下有 10 项端到端回归测试，锁定了：

- 买-持策略的最终现金、持仓、净值与总收益（手算）
- 均线策略的完整往返：买入、次日开盘成交、T+1 卖出、末交易日订单撤销（`BACKTEST_ENDED`）
- `result.save(dir)` + `BacktestResult.load(dir)` 的 CSV/JSON 往返
- 均线策略对公共 API 的隔离（不允许触碰 `_portfolio` / `_event_log` 等内部字段）

如果要切换到真实 CSV 数据源，编辑 `examples/buy_and_hold.py` 里 `_build_portal_inline()`：

```python
from hqbacktest import HqDataCsvPortal
portal = HqDataCsvPortal(source="tushare", data_root="~/.hqdata")
```

`source` 可以是名称（如 `"tushare"`）或绝对路径（如 `"/home/<user>/.hqdata/tushare"`），底层 CSV 布局由 hqdata CLI 在回测前写入；hqbacktest 不会下载数据。

## 测试与开发

项目的验证入口为：

```bash
pytest tests/ -v
```

`tests/examples/` 中的端到端测试会再次 `python examples/*.py` 作为子进程，确认示例在干净环境下也能跑。单元测试必须使用内存数据或 mock，不依赖网络和本地行情文件。确需在真实数据上验证 Tushare 或 RiceQuant 适配的集成测试必须在 `~/.hqdata/{name}` 目录不存在或不可读时自动跳过。每一项公开 API、订单规则、时间语义和绩效公式都应有可手算的回归测试。

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
