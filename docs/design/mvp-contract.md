# hqbacktest 产品契约

> 状态：**已固化**。本文档是领域术语、模块边界、日事件顺序、数据可见性、订单成交时机、精度规则和非目标的**唯一事实来源**。
>
> 阅读对象：参与 `hqbacktest` 实现的开发者、审阅者与 AI 助手。任何对默认契约的修改必须先更新本文并在第 9 节登记。

## 1. 目的与版本

- 本文档是 `hqbacktest` 的设计层契约：定义"是什么 / 何时 / 如何"。`README.md` 面向用户说明"怎么用"。
- 本文档面向 `hqbacktest` 的首个可用版本（v0.1）；后续版本（指数基准、分钟线、多账户等）必须以独立章节或独立文档承接，不得通过单点补丁混进本文。
- 修订规则：任何对默认值、术语、事件顺序或不可变规则的修改，必须**先**改本文，再同步调整 README，并在第 9 节追加一条变更记录。

## 2. 术语表

| 术语 | 定义 |
| --- | --- |
| 交易日 | 数据源交易日历中标注为开市的自然日，记作 `D`，格式 `YYYYMMDD`。 |
| 回测日 | 引擎当前正在处理的交易日；同一时刻只有一个回测日。 |
| 撮合日 | 订单首次满足撮合条件并实际撮合的交易日。`BEFORE_TRADING_START(D)` 创建的订单在 `OPEN_MATCH(D)` 撮合；`BAR_CLOSE(D)` 创建的订单最早在 `OPEN_MATCH(D+1)` 撮合。 |
| 股票代码 | 形如 `600000.SH`、`000001.SZ`、`688001.SH` 的字符串，前缀 6 位为数字，后缀 `.SH`/`.SZ` 为交易所。指数代码不属于 v0.1 可交易标的。 |
| 股票池（universe） | 策略本次回测关注的标的集合；由策略在 `initialize` 中声明，是数据访问与产生下单意图的范围，不等同于当日可交易资格。 |
| 订单（Order） | 策略提交的成交意图，本身不修改账户。 |
| 成交（Fill） | 订单被撮合后产生的不可变账本条目，含价格、数量、费用和关联订单 ID。 |
| 持仓（Position） | 账户在某回测日对某标的的余额与可卖数量；区分当日买入与历史持仓。 |
| 账本（Portfolio） | 现金、持仓、冻结现金、已实现盈亏和未实现盈亏的合计；策略不可直接修改。 |
| Context | 引擎向策略暴露的只读查询接口与受控下单入口。 |
| DataView | 引擎向策略暴露的数据视图，带 `visible_through` 截止日；越过截止日必须抛错。 |
| AdjustmentPolicy | 复权与公司行为调整策略；v0.1 仅支持 `none`。`factor_total_return` 必须等待独立的公司行为会计设计和验证后才可加入。 |
| 数据源（source） | 一次回测固定的 hqdata CSV 数据源名称，如 `tushare`、`ricequant`。数据集根目录为 `{data_root}/{source}`，其中 `data_root` 默认 `~/.hqdata`，可在回测配置中覆盖；同一次回测不混用数据源。 |

## 3. 默认契约表

### 3.1 业务范围

| 维度 | v0.1 默认决定 |
| --- | --- |
| 市场与频率 | A 股普通股票的**日线**回测；先支持沪深普通股票。北交所、ST、上市首日无涨跌幅限制等特殊证券延后。 |
| 账户 | 单账户、人民币现金、现货多头；不支持融资融券、做空、期货、期权、组合级保证金。 |
| 数据边界 | 每次运行只读取一个 hqdata 已落盘数据源的 CSV 快照；`data_root` 默认 `~/.hqdata`，`source` 选择其下的子目录。门户通过 `hqdata.api.get_*` 调用统一的 `CsvSource` 读取稳定布局的 CSV —— CSV 列映射、文件存在性与列名校验由 hqdata 负责，回测侧只把 DataFrame 转 `Bar/Factor`。hqbacktest 不导入 `hqdata.sources` 或任一数据源 SDK、不在回测运行时访问网络。日线首选 Tushare 或 RiceQuant 的本地 CSV。 |
| 时间语义 | `before_trading_start(D)` 只能看到 D-1 及以前的数据，可提交在 D 开盘撮合的订单；`on_bar(D)` 在 D 收盘后看到 D 日线，订单最早在 D+1 开盘撮合。 |
| 初始订单 | 首版只支持市价委托；默认按符合交易条件的开盘价全额成交。限价单、分笔、成交量参与率、盘中撮合均属后续能力。 |
| 数据可见性 | 策略读取数据必须经过带 `visible_through` 截止日的 `DataView`；任何未来数据访问必须抛错。 |
| 复权与公司行为 | 下单、现金、成交账本和 v0.1 净值使用未复权价格，且 `adjustment_policy` 固定为 `none`。因子仅可在同一数据源内计算相邻交易日比值，但不据此改变现金、持仓、可卖数量、成本价或净值；精确公司行为与因子总回报口径延后实现。`CorporateActionProvider` 为设计草案（不实现）：`actions_for` 必返回含 `ex_date` / `cash_dividend_per_share` / `stock_dividend_ratio` / `rights_ratio` / `rights_price` / `tax_rate` / `conversion_ratio` / `fractional_share_handling` / `note` 等权威字段的对象；任一字段缺失时不得构造会计分录。 |
| 结果 | 至少产出净值曲线、订单、成交、每日持仓、交易成本和基础绩效指标；结果必须能追溯到配置、代码版本和数据来源。 |

### 3.2 数据类型与精度

| 数据 | 类型 | 说明 |
| --- | --- | --- |
| 日期 | 字符串 `YYYYMMDD` | 输入、配置、事件日志与结果文件统一使用 8 位字符串；内部可临时转为 `datetime.date`。 |
| 股票代码 | 字符串 | 形如 `600000.SH`、`000001.SZ`、`688001.SH`；交易所后缀大写。 |
| 金额与价格 | `decimal.Decimal` | 构造时禁止由二进制浮点直接转换；字符串/整数构造优先。金额以人民币元计，价格以元/股计。 |
| 数量 | 整数（股） | 不允许小数股；买入数量按 100 股整手向下取整，卖出可清仓零股。 |
| 收益率 | 浮点（`float`） | 仅在指标计算阶段使用；底层账本不得出现浮点金额。 |
| 比率 | `Decimal` | 佣金率、印花税率等费率配置使用 `Decimal`，避免浮点累计误差。 |

## 4. 日交易日事件顺序

每个交易日 `D` 在引擎内依次经过以下阶段，**阶段顺序不可重排、不可合并**：

| 顺序 | 阶段 | 可见数据 | 可下单 | 市价单最早成交时间 |
| --- | --- | --- | --- | --- |
| 1 | `SESSION_START(D)` | 无（仅上下文元数据） | 否 | — |
| 2 | `BEFORE_TRADING_START(D)` | D-1 及以前 | 是 | D 开盘 |
| 3 | `OPEN_MATCH(D)` | —（撮合内部阶段） | 否 | — |
| 4 | `BAR_CLOSE(D)` | D 日线（含 OHLC、成交量） | 是 | D+1 开盘 |
| 5 | `AFTER_TRADING_END(D)` | D 收盘后的账户与持仓快照 | 否 | — |

时序示意：

```text
      D-1 收盘                 D 开盘      D 收盘
          │                       │           │
          └──── BAR_CLOSE(D-1) ───┘           │
                  │                            │
                  ▼                            │
   BEFORE_TRADING_START(D)   ◀── 策略看到 D-1 ──┘
                  │
                  ▼
        提交订单（当日开盘撮合候选）
                  │
                  ▼
           OPEN_MATCH(D) ─── 开盘价全额成交
                  │
                  ▼
            BAR_CLOSE(D) ─── 策略看到 D 收盘
                  │
                  ▼
       提交订单（最早 D+1 开盘撮合）
                  │
                  ▼
      AFTER_TRADING_END(D) ─── 日终估值与回调
```

配套约束：

- `on_bar` 与 `before_trading_start` 由策略分别实现；缺省实现必须存在，引擎不可隐式假设策略提供。
- `AFTER_TRADING_END(D)` 使用 D 的有效未复权 `close` 计算市值和净值。若持仓标的没有有效收盘价，运行失败并记录数据错误；v0.1 不使用前收、插值或静默跳过估值。
- 回测结束日的 `BAR_CLOSE(end_date)` 后，所有尚未成交的有效订单统一转为 `CANCELLED`，理由为 `BACKTEST_ENDED`；引擎不得为了成交订单擅自延长回测日期。
- 当日可交易资格由“策略股票池、回测日历史股票池、交易规则和有效行情”共同决定。未上市/已退市标的的订单必须显式拒绝；停牌标的可留在股票池中，但不可成交。
- `Context.set_universe()` 只允许在 `initialize` 中调用；v0.1 的股票池在运行开始后不可变。
- 策略或基础设施异常必须携带日期、阶段、原始异常信息，且不得留下半交易日状态（详见规则 12）。单笔业务拒绝（如资金不足）不是运行异常，必须记录后继续执行。
- 事件日志必须按时间顺序追加 `日期 / 阶段 / 订单或成交 ID / 错误原因` 字段。

## 5. 模块依赖图

业务实现不得产生反向依赖；`BacktestEngine` 是编排者，可同时依赖下列协议和服务。领域模型只能依赖标准库与同层模型，禁止依赖策略或数据源实现：

```text
        ┌────────────┐
        │  strategy  │
        └─────┬──────┘
              ▼
        ┌────────────┐
      │ engine /   │
      │ context    │
        └─────┬──────┘
              ▼
   ┌─────────────────────┐
   │ broker / portfolio  │
   └─────────┬───────────┘
             ▼
   ┌─────────────────────┐
   │   data portal       │
   └─────────┬───────────┘
             ▼
        ┌────────────┐
      │ hqdata CSV │
        └────────────┘
```

边界规则：

- `strategy` 只能依赖 `engine/context` 暴露的 `Context`、`DataView` 与生命周期回调；不得导入 `hqdata.*`、不得持有 `MarketDataPortal` 的原始实现。
- `engine` 编排 `MarketDataPortal`、`DataView`、`broker`、`portfolio` 与策略生命周期；它通过 `MarketDataPortal` 读取交易日历，并向 `broker` 提供窄化的撮合行情接口。
- `broker/portfolio` 只能由 `engine` 驱动；不得反向调用策略或回写 `Context`。`broker` 不负责日历迭代或策略调度。
- `data portal` 只暴露协议化的 `MarketDataPortal`；`HqDataCsvPortal` 是默认实现，只读取 hqdata CLI 已落盘 CSV，禁止导入 `hqdata`、`hqdata.sources` 或任一数据源 SDK。
- `data portal` 通过 `data_root` 与 `source` 解析数据集根目录。v0.1 的固定布局为 `{root}/{source}/calendar.csv`，以及 `stock_list/{YYYYMMDD}.csv`、`stock_daily/{YYYYMMDD}.csv`、`stock_factor/{YYYYMMDD}.csv`；任何缺失、不可读或格式不符的文件必须报错，不得联网回补。
- hqdata CSV 快照是叶子数据边界；更新数据只能在回测运行前通过 hqdata CLI 完成。
- 回测侧通过 `hqdata.api` 读取 CSV；任何自定义源（替代 `CsvSource`）必须保持同等的列名契约与缺失语义，否则替换需要回到本节同步调整。

### 3.3 数据可见性与缺行语义

| 维度 | v0.1 默认决定 |
| --- | --- |
| `get_bars(symbol, start, end)` | 返回窗口内实际存在的行，**允许逐日间隙**；窗口内无任何行返回 `[]` 而非报错。 |
| `get_bars` / `get_factor` 失败分类 | 个股当日缺行（停牌 / 未上市 / 已退市）→ 静默从结果集中省略（窗口内可能有 N 行，绝不报错）；**整日快照文件缺失** → `SnapshotFileMissingError`（`MissingDataError` 子类），引擎不得当作「该股无价」处理，必须以 `DATA_ERROR` 中止本次运行。`MarketDataPortal` **没有**单点 `get_bar(symbol, date)` 接口——单日查询由 `current_price(symbol)` 与 `get_bars(symbol, d, d)` 共同覆盖。 |
| `current_price(symbol)` | 返回截至 `visible_through` 的最近一个有效收盘价，**回看上限 20 个交易日**，超出返回 `None`；停牌持仓按最近收盘估值并记录 `DATA_WARNING` 事件，禁止静默按 0 计入。 |
| 首个交易日盘前（`visible_through="00000000"`） | `history` 返回 `[]`、`current_price` 返回 `None`，**不抛异常**。 |
| `get_universe(date)` | **按精确日期查询**，不做向前回退；`.BJ`（北交所）股票默认过滤，可通过 `include_bj=True` 保留。 |
| `Bar.volume` 单位 | **手**（1 手 = 100 股；与 Tushare `hqdata` 适配器口径一致）；调用方需要股数时应乘以 `LOT_SIZE`。 |
| 双门户一致性 | `InMemoryDataPortal` 与 `HqDataCsvPortal` 行为完全一致（parity 测试覆盖）。 |

### 3.4 撮合与账本口径

| 维度 | v0.1 默认决定 |
| --- | --- |
| 同日撮合顺序 | 单个 `OPEN_MATCH(today)` batch 内**所有 SELL 先撮合、再撮合 BUY**（A 股「卖出资金当日可用」），同侧内保持策略提交顺序；`broker.match` 按此顺序返回结果以保证 `running_cash` 滚动检查生效。 |
| 资金检查 | `InsufficientCashRule` 检查**滚动现金**（running_cash）：SELL 净回款增加、BUY 成本扣除，拒绝后不滚动。 |
| 整手取整 | **仅 BUY** 按 100 股整手向下取整；SELL 允许任意正整数股（含零股），静默截到整手违反契约。 |
| `order_target(symbol, 0)` | 必须能清仓含零股的持仓；`order(sym, -N)` 对任意正整数 N 提交原数量。 |
| T+1 可卖不足 | **整单拒绝**（`RejectReason.INSUFFICIENT_SHARES`），不支持部分截断。 |
| `realized_pnl` | **不含任何费用**（commission / stamp_tax / other_fee），仅 `(sell_price - avg_cost) × quantity`；费用只走现金账。 |
| 金额量化 | 全部使用 `ROUND_HALF_EVEN`（`quantize_cash` 0.01 元 / `quantize_price` 0.0001 元），与券商「四舍五入到分」存在 1 分级差异。 |
| 同日同价「先买后卖」与「先卖后买」 | 提交顺序决定成交时点；`realized_pnl` 在费用外对相同 `(price, avg_cost, quantity)` 相同，**不含费用的现金额**依提交顺序而不同。 |
| BUY 成交 `stamp_tax` | 必须为 0（印花税仅在 SELL 收取），`Fill.__post_init__` 校验拒绝非零。 |
| `target_quantity_for_value(0)` | 返回 `0`（flatten），与 docstring 一致。 |
| CLI `initial_cash` | 拒绝 `float`（TOML 字面 `100000.0` 报错），与引擎层 `BacktestConfig` 严格度对齐。 |

### 3.5 净值与绩效指标口径

| 维度 | v0.1 默认决定 |
| --- | --- |
| 首日 `daily_return` | `total_equity[0] / initial_cash - 1`（**不再硬编码 0**），首日 P&L 进入收益序列。 |
| 首日 `drawdown` | `(initial_cash - total_equity[0]) / initial_cash`（首日下跌时为正；不再硬编码 0），后续日 running peak = `max(initial_cash, 历史 total_equity)`，首日跌幅进入回撤峰值序列。 |
| 恒等式 | `∏(1 + daily_return) == 1 + total_return`（Decimal 精度内）。 |
| 波动率样本不足 | `< 2` 个日收益时 `daily_volatility` / `annualized_volatility` / `sharpe_ratio` 返回 `None` + note（**禁止错报 0**）；真正 0 波动率才返回 `Decimal('0')`。 |
| `annualized_return` 幂运算 | `float(growth) ** float(exponent)` 通过 `Decimal(str(...))` 重建为 Decimal，**禁止 `Decimal(float(...))`**。 |
| Decimal 量化 | 所有 `float` 桥接的 Decimal 输出统一 quantize 到 `Decimal('0.000000000001')`，保证 `summary.json` 干净。 |
| `positions.sellable_quantity` | **结转后**（D 行快照为 D+1 起始时可卖数）；engine 在 `_snapshot_equity` 前调用 `settle_t1`，D 行的 `sellable_quantity` 即已包含当日成交的滚动。 |

### 3.6 策略隔离与审计完整性

| 维度 | v0.1 默认决定 |
| --- | --- |
| `Order` 不可变 | `@dataclass(frozen=True)`，`fill_ids: tuple[str, ...]`；策略收到 `pending_orders()` 后无法修改任何字段（quantity / avg_fill_price / fill_ids 等）；`transition` / `record_fill` 用 `object.__setattr__` 写入冻结字段，仅 engine / broker 可调用。 |
| `DataView.portal` 私有 | 字段名 `_portal`（下划线私有约定），策略**无法通过公开 API**（`view.portal`）访问——该属性已重命名为 `_portal`，访问旧名抛 `AttributeError`。该约定是 Python 的"约定私有"语义，**不**是语言级强制力：策略仍可经 `view._portal`（或 `context._data_view._portal`）触及 raw portal——这是 Python 语言限制而非本项目缺陷；契约依靠约定 + 审计测试守住。所有合规数据访问走 `view.history` / `view.current_price` / `view.universe`。 |
| Universe 生效 | `set_universe(...)` 后，对未声明的符号下单立即拒绝（`RejectReason.OUT_OF_UNIVERSE`，含 ORDER_CREATED + ORDER_REJECTED 事件，Order 不经过 broker、停留在 `_out_of_universe_orders` 并在 result 构造时折入 `orders_table`）；**未设 universe 时不限制**。 |
| 历史股票池 | `Context.historical_universe()` 返回 `visible_through` 当日的 portal 股票池（默认排除 `.BJ`），受可见性约束；不暴露 raw portal。 |
| 返回值防御性 | `pending_orders()` / `universe()` / `historical_universe()` 均返回 list 副本；Bar / Factor 跨查询复用。 |

### 3.7 因子诊断接入与分红偏差显性化

| 维度 | v0.1 默认决定 |
| --- | --- |
| 启用条件 | `adjustment_policy=none`（v0.1 唯一接受值）；引擎对**当前持仓**（quantity > 0）标的的因子跳变自动诊断，清仓后停止（持有期结束）。 |
| 跳变阈值 | **0.1%**（holdings-period 阈值，`jump_band=(0.999, 1.001)`）；`analyze_factor_series` 默认 `(0.5, 2.0)` 用于一般因子质量诊断，holdings-period 用更严阈值。 |
| 数据来源 | `portal.get_factor(symbol, today, today)`；快照缺失 / 无因子静默跳过（不影响 run），仅在**当前持仓**且超出阈值时产出警告。 |
| 写入位置 | `EngineEvent` 阶段 `DATA_WARNING`（detail 包含前后因子值与 ratio）；`FactorDiagnosticCollector` → `BacktestResult.factor_diagnostics` → `summary.json` 的 `factor_diagnostics` 字段。 |
| 账本影响 | **零**：诊断是只读观测，绝不修改 cash / position / equity；baseline 与诊断版的逐字节相同（byte-identical ledger）。 |
| CLI 警告 | `run_from_config` 末尾若 `result.factor_diagnostics` 非空，打印一行 `warning: N corporate-action factor jumps detected during holding periods; NAV excludes dividends (adjustment_policy=none), see summary.json`。 |
| 文档承诺 | README 显著位置明示：`adjustment_policy=none` 下跨除权日的净值**系统性低估**（少分红现金），长区间结果不可用于收益评估，并链接因子诊断输出（`summary.json` / `events.jsonl`）。 |

### 3.8 CLI 易用性与契约承诺一致性

| 维度 | v0.1 默认决定 |
| --- | --- |
| 策略模块解析 | `hqbacktest run` 把 config 文件所在目录和当前工作目录加入 `sys.path`（与 `python -m hqbacktest run` 行为一致），`[strategy].module = "my_strategy"` 这类不带点号的写法直接可 import。 |
| `initial_cash` 校验 | 拒绝 `nan` / `+inf` / `-inf` / `float`；接受 `int` / `str` / `Decimal`；错误为单行 `ConfigError`（CLI exit 2）。 |
| 日期校验 | `YYYYMMDD` 格式 + 真实日历；`20241399` 等非法日期立即拒。 |
| 空交易窗口 | `[start, end]` 区间在 portal 日历上无交易日 → `ConfigurationError`，不得静默成功写出空结果。 |
| 输出目录复用 | 已存在且非空的输出目录默认拒绝（CLI exit 3）；`--force` 可覆盖。 |
| `order_value` 等下单函数 | 接受 `int` / `str` 金额（继续拒绝 `float`），降低策略样板代码。 |
| `git_commit` 语义 | `run_metadata.json` 记录 **hqbacktest 自身** 的 git commit（engine 来源），不再记录用户 cwd 仓库 commit。 |
| 文档一致性 | README「命令行」「错误信息」章节逐条与实现对齐；包布局含 `cli/` 子包；CLI 错误示例覆盖 exit 2/3/4 各档。 |

## 6. 不可变规则

以下 13 条规则在 v0.1 期间**必须严格生效**，不可通过代码路径绕开；新增能力时若必须突破某条，必须先在本文档登记例外并同步 README。

1. **无未来函数**：策略在任意阶段只能读到第 4 节"可见数据"列允许的数据；越界访问必须在 `DataView` 层抛错，不得由引擎静默裁剪或填充。
2. **单数据源**：一次回测只允许一个 `hqdata` 数据源；中途切换或混用必须在配置层直接拒绝。
3. **T+1 可卖**：当日买入的标的，最早次日（按交易日历）方可卖出；账本必须区分"今买"与"历史持仓"。
4. **整手买入**：买入数量按 100 股整手向下取整；零股只能卖出，且卖出数量不得超过 `min(可卖持仓, 订单数量)`。
5. **Decimal 精度**：现金、价格、手续费、市值在账本层一律使用 `decimal.Decimal`；禁止 `float(...)` 构造 Decimal，禁止浮点金额进入账本字段。
6. **缺失或非法价必须拒绝**：撮合时若无当日开盘价、停牌、或价格为 `NaN`/零/负，订单必须进入显式拒绝状态，不得默认按 0 元或前一日收盘价成交。
7. **限价单显式拒绝**：v0.1 不接受限价单、止损单、成交量参与率；策略若提交必须立即抛 `UnsupportedOrderType`，不得悄悄降级为市价单。
8. **因子不得伪造公司行为**：复权因子只能在同一数据源内按相邻交易日比较；v0.1 可记录跨源、零、负或缺失因子的诊断信息，但不得据此修改现金、持仓、可卖数量、成本价或净值。精确公司行为需要独立权威数据。
9. **AdjustmentPolicy 必须显式且受限**：v0.1 配置必须显式指定 `none`；`factor_total_return` 等其他值必须在配置校验时拒绝。新增策略前必须定义其会计分录、估值公式、卖出处理和可手算测试。
10. **事件日志完整可追溯**：每一笔订单、成交、拒绝、调整必须写入事件日志，且至少包含 `日期 / 阶段 / 订单或成交 ID / 拒绝或调整原因`；缺失任一字段视为违反契约。
11. **Context 只读、不可改写**：策略只能通过 `Context` 的查询方法读取现金、持仓、订单等；任何对 `Portfolio` 字段的直接赋值或不通过 `broker` 的修改必须抛错。
12. **异常不得留下半交易日状态**：策略异常、数据校验错误和经纪商内部错误必须终止本次运行（或标记为失败运行），不得留下半交易日账本或静默吞掉异常；单笔订单的业务拒绝必须保留账本不变、记录原因并继续运行。
13. **DataView 越界即报错**：读取 `visible_through` 之后的数据必须立即抛错；不得返回空值、最后已知值或插值结果。

## 7. 职责边界矩阵

| 职责 | 归属 | 说明 |
| --- | --- | --- |
| 策略信号序列 | `strategy` | 仅负责产出"买/卖多少"的意图。 |
| 撮合价格 | `broker` | 按当日开盘价（市价单）撮合；价格来自 `MarketDataPortal`。 |
| 公司行为明细 | 不在 v0.1 | 精确的现金分红、送配、配股、税费另设 `CorporateActionProvider`。 |
| 复权因子 | `data portal` | 可暴露同源因子序列用于数据诊断；v0.1 不将因子写入账本或净值。 |
| 现金与持仓账本 | `portfolio` | 由 `broker` 调用更新；策略不得直接访问。 |
| 交易日历与历史股票池 | `data portal` | 按回测日查询，禁止以当前股票列表替代。 |
| 指标与报表公式 | `result` | 公式与配置来源必须在文档中登记。 |

## 8. 非目标（首版明确不做）

- 实盘下单、券商连接、实时行情或风控托管。
- 分钟线、Tick、盘中回测或异步多市场时钟。
- 多账户、多币种、融资融券、卖空、期货、期权和期权希腊值。
- 没有可靠证券状态数据支撑的 ST、停牌细则、首日、新股和北交所全部规则。
- 仅凭复权因子伪造精确的现金分红、送配、配股或税务明细。
- 在 `hqdata` 尚未提供指数日线前，将基准收益率作为引擎的必备功能。

## 9. 修订记录

| 日期 | 变更 | 作者 |
| --- | --- | --- |
| 2026-08-17 | 初稿：固化术语、模块边界、日事件顺序、数据可见性、订单成交时机、精度规则和非目标 | hqbacktest 维护者 |
| 2026-08-17 | 修正盘前订单的同日开盘撮合语义；明确收盘估值、结束订单、股票池资格与异常分类；v0.1 仅支持 `AdjustmentPolicy=none` | hqbacktest 维护者 |
| 2026-08-23 | 公司行为扩展设计门槛落地——`adjustment_policy` 严格只接受 `"none"`；`CorporateActionProvider` 列为设计草案并锁定 10 个权威字段；`factor_diagnostics` 字段已就位；因子诊断接口存在但 v0.1 不启用 | hqbacktest 维护者 |
| 2026-08-23 | 修正回测运行时数据边界：`hqbacktest` 直接只读 hqdata CLI 落盘 CSV；`data_root` 默认 `~/.hqdata`，不调用 `hqdata.api` 或网络数据源 | hqbacktest 维护者 |
| 2026-08-26 | 改造数据层契约：hqbacktest 改为通过 `hqdata.api` 的 `csv` source 读取 snapshot（不再直读 CSV），DataFrame → Bar/Factor 转换与双层缓存在 hqbacktest 侧；新增 `hqdata.errors.SnapshotFileMissingError` 透传路径；Calendar 缺失返回空（对齐 tushare）。CSV 列名校验全部移交 `hqdata.sources.csv_source` | hqbacktest 维护者 |
| 2026-08-23 | 重构数据门户：`HqDataPortal` 替换为 `HqDataCsvPortal`，固定布局 `{data_root}/{source}/calendar.csv` + `stock_list|stock_daily|stock_factor/{YYYYMMDD}.csv`；`source` 名称或绝对路径均可，`CacheKey` 加入 `data_root` 防跨目录串扰 | hqbacktest 维护者 |
| 2026-08-24 | 数据层缺行/停牌/首日语义：钉死 `get_bars` 允许间隙、引入 `SnapshotFileMissingError` 区分整日文件缺失与个股缺行、`current_price` 回看 20 交易日最近有效收盘价、首日哨兵日期不抛异常、删除 `InMemoryDataPortal.get_universe` 向前回退、补双门户 parity 测试、缓存返回防御性拷贝、`.BJ` 股票默认过滤、`Bar.volume` 单位标注为「手」 | hqbacktest 维护者 |
| 2026-08-24 | 撮合与账本语义：同批撮合 SELL 先于 BUY（滚动现金）、SELL 不整手取整、`Fill.BUY` 携带非零 stamp_tax 报错、`Order.record_fill` 移除不可达 `ACCEPTED` 分支、`intents.target_quantity_for_value(0)` 按 docstring 返回 0、CLI `initial_cash` 拒绝 float 与引擎对齐、`realized_pnl` 不含费用；登记 §3.4 撮合口径表 | hqbacktest 维护者 |
| 2026-08-24 | 净值与指标基准：首日 `daily_return` / `drawdown` 以 `initial_cash` 为基准、后续日 running peak = `max(initial_cash, 历史 total_equity)`、波动率样本不足返回 `None` 而非 0、`Decimal(str(float(...)))` 替代 `Decimal(float(...))` 幂运算桥接、`positions.sellable_quantity` 口径登记为「结转后」；恒等式 `∏(1 + daily_return) = 1 + total_return` 成立；登记 §3.5 | hqbacktest 维护者 |
| 2026-08-24 | 策略隔离与审计完整性：`Order` 改为 `frozen=True`、`DataView.portal` 改为私有 `_portal`、universe 生效（`RejectReason.OUT_OF_UNIVERSE`）、`Context.historical_universe()` 转发 `DataView.universe()` 受可见性约束；登记 §3.6 | hqbacktest 维护者 |
| 2026-08-24 | 因子诊断接入与分红偏差显性化：engine 在持仓/成交标的的因子跳变（阈值 0.1%）自动生成 DATA_WARNING + `FactorDiagnostic`，结果写入 `summary.json` / `events.jsonl`；CLI 末尾打印汇总警告；账本与净值完全不变；登记 §3.7 | hqbacktest 维护者 |
| 2026-08-24 | CLI 易用性与文档真实性：console script 把 config dir + cwd 加入 sys.path；`initial_cash` 拒绝 nan/inf/float；空交易窗口、空输出目录、`--force` 覆盖；`order_value` 接受 int/str；`git_commit` 改为 hqbacktest 自身版本；README 错误码表与包布局对齐；登记 §3.8 | hqbacktest 维护者 |
| 2026-08-25 | `source` 绝对路径支持（拆为 `data_root` + 名称）；`run_metadata.json` 中 `config_path` / `output_directory` / `config_output_directory` 写入相对路径（`os.path.relpath`）；`validate_yyyymmdd` 用 `datetime.strptime` 拒绝假日期（保留 `"00000000"` 哨兵）；性能夹具生成器改用 `datetime` 迭代；`test_console_script_runs_end_to_end` 改名 `test_python_m_runs_end_to_end` 并补一个真正测 console script 的同名测试；README「26 项 CLI 测试」改为「见 tests/cli/」；`pyproject.toml` 删除过时的「no runtime deps yet」注释 | hqbacktest 维护者 |
| 2026-08-25 | 波动率/夏普首日采样缺口修复：`metrics.compute_metrics` 不再从 `total_equity` 重新推导日收益（旧零种子会丢首日真实收益），改为直接读 `engine` 写好的 `EquityPoint.daily_return`；删除死代码 `_drawdown_series`；新增手算回归（2 日 -9% / +5.5%，`daily_volatility` ≈ 0.10253）。波动率 / Sharpe 与 `max_drawdown` 对首日盈亏的可见性现在一致 | hqbacktest 维护者 |
| 2026-08-25 | 数据层测试覆盖补齐与文档措辞澄清：6 项 `get_factor` 双门户逐值一致性断言；§3.3 删除不存在的 `get_bar(symbol, date)` 引用；`DataView.portal` 措辞改为准确表述（下划线是约定私有，不是 Python 语言级强制力）；哨兵常量 `"00000000"` 收敛到 `data.validators.SENTINEL_NO_HISTORY` 一处；`test_version_matches_pyproject` 强化版本号形态校验 | hqbacktest 维护者 |