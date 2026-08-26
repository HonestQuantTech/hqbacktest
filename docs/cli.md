# 命令行

> 适用版本：v0.1。`hqbacktest run` 命令读取 TOML 配置，运行回测，将结果写入独立目录。

## 1. 安装 / 入口

`hqbacktest` 通过 `pyproject.toml` 的 `[project.scripts]` 注册为 console script：

```bash
hqbacktest run --config configs/moving_average.toml --output results/moving-average
# 等价：
python -m hqbacktest run --config configs/moving_average.toml --output results/moving-average
```

### 1.1 策略模块解析

`hqbacktest run` 把 config 文件所在目录和当前工作目录加入 `sys.path`，让 `[strategy].module = "my_strategy"` 这类**不带点号的写法**可以直接 `import` 成功，行为与 `python -m hqbacktest run` 一致。

### 1.2 `--output` 覆盖

- `--output` 可选；省略时使用配置中 `[output].directory`。
- 提供时覆盖该值（例如 CI 把结果重定向到临时目录）。

### 1.3 `--force`

输出目录已存在且非空时默认拒绝（exit 3）；`--force` 强制覆盖：

```bash
hqbacktest run --config FILE --output DIR --force
```

## 2. 配置 schema

```toml
[start]
start_date = "20240102"        # YYYYMMDD，必填
end_date   = "20240105"        # YYYYMMDD，必填

[capital]
initial_cash = "100000"        # Decimal 字符串，必填，>= 0

[data]
source = "tushare"             # 数据源名称或绝对路径，必填
data_root = "~/.hqdata"        # 可选；默认 ~/.hqdata

[strategy]
module = "examples.buy_and_hold"   # 可导入的 Python 模块，必填
class_name = "BuyAndHold"          # 可选；省略时使用模块内第一个 BaseStrategy 子类
[strategy.kwargs]                  # 可选：传给策略构造函数的参数表
answer = 42

[output]
directory = "results/run-1"       # 必填；不存在则创建；可被 --output 覆盖
```

`[cost_model]` 可选；省略时使用 v0.1 默认费率（见下表）。

### 2.1 `[cost_model]`

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `commission_rate` | `"0.00025"` | 0.025%，Decimal 字符串 |
| `min_commission` | `"5.00"` | 5 元保底 |
| `stamp_tax_rate` | `"0.001"` | 0.1%，仅 SELL |
| `transfer_fee_rate` | `"0.0"` | 过户费（v0.1 默认 0） |

### 2.2 `source` 路径解析

| 写法 | 含义 |
| --- | --- |
| `source="tushare"` | 裸名，解析为 `{data_root}/{source}` |
| `source="/home/me/.hqdata/tushare"` | 绝对路径，拆为 `(parent_dir, basename)` |
| `source="~/.hqdata/tushare"` | 绝对路径同上（同 `pathlib.Path` 行为） |

不能在一次回测中混用多个数据源。

### 2.3 验证规则

任何未知 section 或 key 触发 `ConfigError` + 退出码 2。详细验证见 §4「错误信息」。

## 3. 输出目录

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

每个文件的 schema 与字段说明见 [`docs/output.md`](output.md)。

### 3.1 `run_metadata.json`

| 字段 | 来源 | 示例 |
| --- | --- | --- |
| `hqbacktest_version` | `hqbacktest.__version__` | `"0.1.4"` |
| `python_version` | `sys.version` | `"3.11.5 ..."` |
| `platform` | `platform.platform()` | `"Linux-..."` |
| `timestamp_utc` | `datetime.utcnow().isoformat()` | `"2026-08-25T03:11:42+00:00"` |
| `git_commit` | **hqbacktest 自身**的 git commit（不是用户 cwd） | `"a1b2c3d..."` |
| `config_path` | `os.path.relpath(config_path, cwd)` | `"configs/moving_average.toml"` |
| `output_directory` | `os.path.relpath(output_dir, cwd)` | `"results/run-1"` |
| `data_root` | 配置给定 | `"~/.hqdata"` |
| `adjustment_policy` | 配置给定 | `"none"` |

> `summary.json` **不**写入 token / 完整环境 / 本地绝对路径；`config_path` / `output_directory` 在写入时用相对 cwd 路径脱敏。

### 3.2 `summary.json` 稳定性

`summary.json` 跨运行字节级稳定（去掉 `rule_set` 等含内存地址的运行时对象）；同输入同数据 → 同输出，可用 `diff` 复核差异。`rule_set` 在 `summary` 序列化时被剥离以避免内存地址入文件。

## 4. 错误信息

退出码分布：

| 退出码 | 类别 |
| --- | --- |
| 0 | 成功 |
| 2 | 配置错误 |
| 3 | 输出目录错误 |
| 4 | 引擎异常 |

具体错误一览：

| 情况 | 退出码 | 错误示例（stderr 唯一一行） |
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
| 策略无 `class_name` 且模块无 BaseStrategy | 2 | `no BaseStrategy subclass found in ...` |
| 输出目录不可创建 / 不可写 | 3 | `cannot create output directory ...: ...` |
| 输出目录已含旧结果文件 | 3 | `output directory ... already contains prior-run files; pass force=True ...` |
| 引擎异常（非 RunFailed） | 4 | `backtest run failed: ...` |
| 成功 | 0 | stdout 一行：`hqbacktest: wrote results to results/run-1` |

> 退出码 ≠ 0 时，唯一一行错误输出到 stderr，便于 CI / shell 脚本处理。

## 5. 复现性

两次相同输入 + 相同数据 + 相同 `data_root` 的运行：

- `events.jsonl` 字节相同
- `equity_curve.csv` / `orders.csv` / `fills.csv` / `positions.csv` / `costs.csv` 字节相同
- `summary.json` 字段值相同（去除内存地址等运行时常量后）
- 唯一的非确定性字段是 `run_metadata.json` 的 `timestamp_utc`（记录运行时刻，非结果数据）

CI 用法：

```bash
hqbacktest run --config configs/baseline.toml --output results/baseline
hqbacktest run --config configs/baseline.toml --output results/baseline-2 --force
diff -r results/baseline results/baseline-2   # 仅 run_metadata.json 的 timestamp_utc 不一致
```

## 6. CLI 测试

CLI 测试覆盖端到端、配置验证、可复现性与错误信息，详见 `tests/cli/`。
