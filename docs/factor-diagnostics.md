# 因子诊断与分红偏差显性化

> 适用版本：v0.1。契约层级见 [`docs/design/mvp-contract.md`](design/mvp-contract.md) §3.7。

## 1. 为什么需要这份文档

`adjustment_policy="none"` 下，跨除权日（除息 / 除权日）的回测净值**系统性低估**（少分红现金）。这是数据 + 复权策略的选择决定的，不是 bug：

- 成交、现金账本、v0.1 净值**全部使用未复权价格**。
- 因子复权序列只用于数据质量诊断，不进入账本、不改写 `cash` / `position` / `equity`。
- 因子总回报口径（`factor_total_return`）必须等独立的「公司行为会计」设计与验证完成后才允许加入配置。

v0.1 的合约做法：**让偏差可见，不让偏差消失**。这份文档说明诊断如何接入、怎样读输出。

## 2. 接入条件

| 维度 | 默认决定 |
| --- | --- |
| 启用条件 | `adjustment_policy="none"`（v0.1 唯一接受值） |
| 触发对象 | **当前持仓**（`position.quantity > 0`）的标的；清仓后停止告警（持有期结束） |
| 跳变阈值 | **0.1%**（holdings-period 阈值，`jump_band=(0.999, 1.001)`） |
| 一般诊断阈值 | `(0.5, 2.0)` 用于一般因子质量诊断（不带 holdings 约束） |
| 数据来源 | `portal.get_factor(symbol, today, today)` |
| 缺失处理 | 快照缺失 / 因子 `None` 静默跳过；不影响 run |

## 3. 数据写入位置

引擎在 holdings 期间遇到因子跳变时同时写入三处：

| 位置 | 内容 |
| --- | --- |
| 事件日志 | `EngineEvent(phase=DATA_WARNING, detail={symbol, before, after, ratio})` |
| `BacktestResult.factor_diagnostics` | 完整 `FactorDiagnostic` 列表：symbol / ex_date / before / after / ratio / action |
| `summary.json` 的 `factor_diagnostics` 字段 | 上列的 JSON 形态 |

代码位置：`src/hqbacktest/engine/factor_diagnostics.py`、`src/hqbacktest/result.py`。

## 4. 账本零影响

诊断是**只读观测**：

- `engine._running_cash` / `Portfolio.position[symbol]` / `EquityPoint.total_equity` 完全不受影响。
- baseline（未启用诊断）与启用诊断版本的 `equity_curve.csv` / `orders.csv` / `fills.csv` / `positions.csv` / `costs.csv` / `summary.json` 字节相同（去掉 `summary.factor_diagnostics` 字段后）。
- 测试锁定：`tests/engine/test_factor_diagnostics.py::test_diagnostics_do_not_change_ledger`。

## 5. CLI 警告

`hqbacktest run`（即 `cli/runner.run_from_config`）末尾检查 `result.factor_diagnostics`：

- 非空 → 打印一行 stderr / stdout 提示：
    ```text
    warning: N corporate-action factor jumps detected during holding periods; NAV excludes dividends (adjustment_policy=none), see summary.json
    ```
- 该提示**不影响退出码**——回测成功仍是 exit 0；诊断的存在不等于运行失败。
- `[--quiet]`（未实现 v0.1 暂不支持）或 `summary.json` 自身的 `factor_diagnostics` 字段是更持久的查询入口。

## 6. 长区间结果的解读

跨除权日长区间（如年度 / 多年）的回测净值：

- **不可**直接用于收益评估，因为它系统性低估现金分红。
- **必须**先评估因子跳变（看 `summary.factor_diagnostics` 与 `events.jsonl` 中的 `DATA_WARNING` 行）。
- 若发现大量 hint 跳变集中于某几个交易日附近，且这些日期符合除权除息节奏，应视为「该结果已被分红偏差影响」的信号。
- 重新评估需要等价的精确公司行为会计能力，**v0.1 不提供**；v0.1 的响应是「看见」而非「修正」。

## 7. API

```python
from hqbacktest.engine.factor_diagnostics import analyze_factor_series

diag: list[FactorDiagnostic] = analyze_factor_series(
    symbol="600000.SH",
    factors=[Decimal("1.0"), Decimal("1.0"), Decimal("0.85")],   # 跨日序列
    threshold=(0.999, 1.001),                                    # 0.1%
)
for d in diag:
    print(d.ex_date, d.before, d.after, d.ratio)
```

`threshold` 缺省为 `(0.5, 2.0)`（一般诊断）；holdings-period 引擎内部传 `(0.999, 1.001)`。

## 8. 手算回归

| 场景 | 测试位置 |
| --- | --- |
| 复刻 `600000.SH` 2026-07-16 除权案例 | `tests/engine/test_factor_diagnostics.py::test_600000_2026_07_16_ex_dividend` |
| 诊断不修改账本（byte-identical ledger） | `tests/engine/test_factor_diagnostics.py::test_diagnostics_do_not_change_ledger` |
| 缺因子 / 无效因子静默跳过 | `tests/engine/test_factor_diagnostics.py::test_missing_factor_no_warning` |
| 清仓后停止告警 | `tests/engine/test_factor_diagnostics.py::test_no_warning_after_position_closed` |
| CLI 末尾打印 `warning: N corporate-action ...` | `tests/cli/test_run_factor_warning.py` |
