# 性能与内存

> 适用版本：v0.1。本文档说明 `HqDataCsvPortal` 在单次回测中的缓存策略、内存量级与实测基准。

## 1. 双层缓存

`HqDataCsvPortal` 在单次回测中按「按日文件缓存 + 按 symbol 累积序列」两层缓存：

```text
get_bars / get_factor
    │
    ▼
_per_day_cache: dict[date, dict[symbol, Bar|Factor]]    # 单次 run 内每天每文件解析一次
    │
    ▼
_per_symbol_series: dict[symbol, list[Bar]]             # 累积序列，二分查找
```

### 1.1 按日文件缓存

- 每个 `stock_daily/{D}.csv` / `stock_factor/{D}.csv` 在一次运行中最多解析一次。
- 解析结果以 `{date: {symbol: Bar}}` / `{date: {symbol: Factor}}` 形式缓存。
- 跨 symbol 的同一天 CSV 只读取一次（按需 lazy-load）。

### 1.2 按 symbol 累积序列

- 每个 symbol 在内存中维护一个按日期升序的累积序列。
- `get_bars` / `get_factor` 在该序列上做 `bisect` 切片，单次调用 **O(log N)**。
- `DataView.history` 走同一累积缓存，单次 `get_bars` 切片即可。

### 1.3 避开旧实现的热点

旧实现的两个热点在 v0.1 已规避：

- **逐日 `get_bars(day, day)` 往返** → 由 `current_price(symbol)` 单次 `get_calendar`（确定 20 日回看起点）+ 一次 `get_bars` 共同覆盖。
- **Bar 重复构造** → `Bar` / `Factor` 对象在重叠窗口间复用，仅返回列表的防御性拷贝。

代码位置：`src/hqbacktest/data/csv_portal.py`、`src/hqbacktest/data/view.py`。

## 2. 内存量级

| 组成 | 单实例大小 | 估算 |
| --- | --- | --- |
| `Bar`（dataclass） | ~200 B | 量级来自属性数 + Decimal 引用 |
| `Factor`（Decimal） | ~80 B | 单字段，Decimal 字符串存储 |
| 全市场累积（5000 symbols × 139 days） | — | 70 万 Bar ≈ 140 MB |

`_symbol_bars` 累积**只**对真实访问过的 symbol 增长——策略触及的 universe 通常远小于全市场，因此普遍远低于 140 MB。

## 3. 真实数据基准

数据集：`~/.hqdata/tushare`，区间 20260105–20260731，139 个交易日，每个 daily 文件约 5000 行。

| 场景 | 总耗时（含首次数据加载） | 目标 |
| --- | --- | :---: |
| 5 stocks × 139 days MA 策略 | ~7.6 s | < 10 s ✅ |
| 300 stocks × 139 days MA 策略 | ~9.4 s | < 120 s ✅ |

环境：标准 CI（4 vCPU / 8 GiB），冷启动加载所有 daily + factor 文件。结果含 **首次数据加载**，不区分冷热。

代码位置：基准运行入口在 `tests/data/test_performance.py::test_real_data_benchmark`（需 `~/.hqdata/tushare` 可读否则 skip）。

## 4. 性能冒烟测试

`tests/data/test_task15_performance.py`：

- 50 symbols × 250 days 全量 `history(bar_count=20)` 在 15 秒阈值内完成。
- 跑 50 组合（不同 universe / 不同窗口长度），确认累积缓存 + bisect 没有退化为线性扫描。

## 5. 调优建议

- 尽量在 `initialize` 中通过 `set_universe` 限定 symbols——`_symbol_bars` 只对触及的 symbol 累积。
- `history` 单次取够窗口长度（如 20 / 60 / 120），避免多次 5 日短期窗口来回 dispatch。
- 避免在策略内保存 `Bar` 列表到 self.*——直接依赖 `data.history()` 的返回值，让累积缓存复用。
- 大区间回测（>10000 日）+ 全 universe（5000 symbols）跑生产数据时考虑 `data_root` 在 SSD 上；CSV 解析是单线程 IO 瓶颈。

## 6. 不属于性能范围

- **网络数据获取**：`hqbacktest` 不调用任何数据源 SDK、不联网；CSV 必须是 `hqdata` CLI 预落盘的。
- **上市公司重计算**：因子序列的全市场预计算由 `hqdata` 完成，不在回测运行时发生。
- **多进程 / NUMA 优化**：v0.1 单线程；现测基准显示单线程 IO 已足够，不引入并行复杂度。
