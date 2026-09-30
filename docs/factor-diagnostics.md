# 因子诊断与分红偏差显性化

> 适用版本：v0.1。契约层级见 [mvp-contract.md](design/mvp-contract.md) §3.7。

## 1. 目的与边界

v0.1 固定使用 `adjustment_policy="none"`：成交、现金账本、持仓成本和净值均使用未复权价格。复权因子**不**会改变账本。

这意味着跨除权除息日持仓时，若现金分红没有权威公司行为数据支持而未入账，净值会系统性低估。因子诊断的职责是让这类风险在结果中可见，而不是凭因子反推或伪造现金分红、送转或配股分录。

`factor_total_return` 仍是预留概念，当前配置会拒绝它。只有具备权威公司行为数据、明确的会计语义和手算回归后，才可以启用。

## 2. v0.1 的实际运行行为

每个日终，回测引擎只检查**当前仍持有**的标的：

1. 读取当日 `portal.get_factor(symbol, today, today)`。
2. 从本次持仓期间累积的同源因子序列中计算相邻日期的比值。
3. 比值不在 $[0.999, 1.001]$（0.1%）内时，产生 `abnormal_jump` 诊断。
4. 引擎将诊断同时写入事件日志和 `BacktestResult.factor_diagnostics`；CLI 在成功结束后汇总打印一条 warning。

清仓时该标的的持仓期间因子历史会被清除；随后再次建仓时，新的持仓期间不会与旧持仓期间的因子比较。未持有的标的不会被扫描。

| 维度 | 当前行为 |
| --- | --- |
| 启用配置 | `adjustment_policy="none"`，也是 v0.1 唯一允许值。 |
| 触发对象 | 日终数量大于零的持仓。 |
| 引擎阈值 | 因子日比值低于 `0.999` 或高于 `1.001`。 |
| 一般分析器默认阈值 | `analyze_factor_series()` 默认 `[0.5, 2.0]`。 |
| 因子缺失或读取异常 | 诊断路径跳过当日，不中断核心回测。 |
| 账本影响 | 零：不改写 `cash`、`Position`、成本、可卖数、成交或净值。 |

## 3. 输出位置

发生新诊断时，写入以下位置：

| 位置 | 内容 |
| --- | --- |
| `events.jsonl` | 一条 `EngineEvent`：`phase=DATA_WARNING`，`detail` 包含 symbol、诊断日期、前后 factor 和诊断详情。 |
| `summary.json` | `factor_diagnostics` 数组。每条包含 `symbol`、`date`、`kind`、`detail`。 |
| Python API | `BacktestResult.factor_diagnostics`，元素类型为 `FactorDiagnostic`。 |
| CLI stdout | 非空时打印 `warning: N corporate-action factor jumps detected during holding periods; NAV excludes dividends (adjustment_policy=none), see summary.json`。 |

`FactorDiagnostic.kind` 可取：

- `missing`：通用分析器发现预期日期没有因子行。
- `non_positive`：因子为零或负数。
- `cross_source`：调用方显式提供另一个源的 reference 后，二者差异超过容差。
- `abnormal_jump`：同源相邻因子比值超出阈值。

当前引擎的持仓期扫描最常见的是 `abnormal_jump`。它不在运行时做跨源比较，也不会因单日因子缺失自动生成 `missing`；后两类可通过纯函数 API 做离线数据质量检查。

## 4. 解读结果

`factor_diagnostics` 非空并不表示回测失败，也不表示数据一定错误。它表示：持仓期间存在需要人工解释的公司行为或数据质量信号。

对于跨除权日的长期结果：

1. 检查 `summary.json.factor_diagnostics` 与 `events.jsonl` 中的 `DATA_WARNING`。
2. 若信号与除权除息节奏一致，将该运行的净值视为**未包含完整分红现金的价格收益近似**，而不是总回报。
3. 不要根据 factor 的绝对值跨数据源比较；只可使用同一 source 内相邻日期的比值。
4. 若需要可用于总回报评估的结果，应等待权威 `CorporateActionProvider` 与公司行为会计功能，而不是在策略中手工补偿。

## 5. Python API

分析器是纯函数，不读取 portal，也不修改账本：

```python
from decimal import Decimal

from hqbacktest.engine.corporate_actions import analyze_factor_series

diagnostics = analyze_factor_series(
    symbol="600000.SH",
    expected_dates=["20260714", "20260715", "20260716"],
    factors=[
        ("20260714", Decimal("16.59")),
        ("20260715", Decimal("16.59")),
        ("20260716", Decimal("17.38")),
    ],
    jump_band=(Decimal("0.999"), Decimal("1.001")),
)
for diagnostic in diagnostics:
    print(diagnostic.date, diagnostic.kind, diagnostic.detail)
```

引擎实现位于 [engine.py](../src/hqbacktest/engine/engine.py)，诊断模型和纯函数位于 [corporate_actions.py](../src/hqbacktest/engine/corporate_actions.py)。

## 6. 回归覆盖

| 场景 | 测试 |
| --- | --- |
| 持仓穿越 2026-07-16 因子跳变时产生 `DATA_WARNING` | [test_factor_diagnostics.py](../tests/engine/test_factor_diagnostics.py) 的 `test_holding_through_dividend_emits_warning_event` |
| 诊断写入 `BacktestResult` | [test_factor_diagnostics.py](../tests/engine/test_factor_diagnostics.py) 的 `test_holding_through_dividend_records_factor_diagnostic` |
| 诊断不改变账本 | [test_factor_diagnostics.py](../tests/engine/test_factor_diagnostics.py) 的 `test_diagnostics_do_not_change_ledger` |
| 清仓后不再告警 | [test_factor_diagnostics.py](../tests/engine/test_factor_diagnostics.py) 的 `test_sold_symbol_stops_emitting_holding_diagnostics` |
| CLI 输出汇总告警 | [test_factor_diagnostics.py](../tests/engine/test_factor_diagnostics.py) 的 `test_cli_runner_prints_summary_when_diagnostics_present` |
| 纯函数的缺失、零/负、跳变和跨源诊断 | [test_adjustment.py](../tests/engine/test_adjustment.py) 的 `test_analyze_*` |
