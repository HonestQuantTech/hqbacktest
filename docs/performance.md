# 性能与内存

> 适用版本：v0.1。本文档说明 `HqDataCsvPortal` 在单次回测中的缓存策略、内存量级与实测基准。

## 1. 缓存策略

`HqDataCsvPortal` 在单次回测中保留三层缓存：

```text
get_bars / get_factor (Markdown path)
    │
    ▼
_daily_index[date] = {symbol: Bar}        # 按日 Bar / Factor map，跨 symbol 共享
    │
    ▼
_symbol_bars[symbol] = [Bar, ...]          # 按 symbol 累积序列，O(log N) 切片

calendar
    │
    ▼
_calendar: list[(date, is_open)]           # 一次性载入缓存
```

### 1.1 按日 dict 缓存（核心）

每日 **跨 symbol 共享一份** `Bar` / `Factor` map：

- `_daily_index[date] = {symbol: Bar}` 通过 `_read_day_bars(date)` 填充。每日期被任何 symbol 的 `get_bars` 命中后，整日 dict 进缓存；后续不同 symbol 查询同一日直接 hit,不再走 csv。
- `_factor_index[date] = {symbol: Decimal}` 同理,供 `get_factor` 复用。
- hqdata 端 `hqdata.api.get_stock_daily_bar(symbol=None, start=date, end=date)` 一日一次被调,后续 symbol 都走缓存。

实测（`tests/data/test_data_layer_performance.py::test_daily_hqdata_call_at_most_once_per_date`）：

- 5 × 5 次覆盖区间 `[0102, 0104]` 的 `get_bars` 调用 → hqdata `get_stock_daily_bar` 仅 3 次(对应 3 个不同 day)。
- 多个 symbol 的 `get_factor` 类似。

### 1.2 按 symbol 累积序列

- 每个 symbol 维护按日期升序的累积 `Bar` 列表。
- `get_bars` / `get_factor` 在该序列上做 `bisect` 切片,单次调用 **O(log N)**。
- `Bar` 对象在重叠窗口间复用,只有返回 list 是防御性拷贝(`is` 比较验证)。

### 1.3 日历缓存

- `_calendar: list[(date, is_open)]` 一次载入,后续 `get_calendar` / `is_trading_day` / `previous_trading_day` / `next_trading_day` 均基于此 list。
- `_read_calendar()` 用 `hqdata.get_calendar("00000000", "99999999")` 一次性读整个 csv,**门户层缓存**所以一天之内只读一次。

> **注意**：每次 `get_bars` / `get_factor` 触发的 `get_calendar(start, end, is_open=True)`(在 `get_stock_daily_bar` 内部)会让 **hqdata 端** 重读 `calendar.csv` —— 这是 hqdata 端 *no-cache* 设计的副作用，每次调用读一次。如果发现它是瓶颈，需要在 hqdata 端加缓存。

### 1.4 避免的热点

新版相对原实现规避了几个热点：

- **逐日 `get_bars(day, day)` 往返** → 由 `current_price(symbol)` 单次 `get_calendar`(20 日回看起点)+ 一次 `get_bars` 共同覆盖。
- **Bar 重复构造** → `Bar` 对象在重叠窗口间复用(见 §1.2)。
- **CSV 列名解析每次重读** → hqdata 端 `pd.read_csv` 默认推断 dtype,只对核心数值列做 `dtype=str`(避免 `Decimal(float())` 精度损失);且 `_read_day_bars` 内部缓存了 dict 后整个 row→Bar 转换也跳过。

代码位置：`src/hqbacktest/data/hqdata_portal.py`、`src/hqbacktest/data/_converters.py`、hqdata `hqdata/sources/csv_source.py`。

## 2. 内存量级

| 组成 | 单实例大小 | 估算 |
| --- | --- | --- |
| `Bar`（frozen dataclass） | ~200 B | 量级来自属性数 + Decimal 引用 |
| `Factor`（Decimal） | ~80 B | 单字段，Decimal 字符串存储 |
| 全市场累积（5000 symbols × 139 days） | — | 70 万 Bar ≈ 140 MB |

`_daily_index` 只对**实际被查询日期**增长；`_symbol_bars` 只对实际访问过的 symbol 累积——策略触及的 universe 通常远小于全市场，因此普遍远低于 140 MB。

## 3. 真实数据基准

数据集：`~/.hqdata/tushare`，区间 20260105–20260731，139 个交易日，每个 daily 文件约 5000 行。

| 场景 | 单次总耗时（含首次数据加载） | 目标 | 结果 |
| --- | --- | :---: | :---: |
| 5 stocks × 139 days MA 策略（单次） | ~13 s | < 60 s | ✅（`test_real_data_ma_5_symbols` 双跑 26 s 包括 CSV 写出对比） |
| 50 symbols × 250 days `history(20)` | 3.3 s | < 15 s | ✅（`test_perf_smoke_50_symbols_250_days_history`） |

环境：开发机（4 vCPU / 8 GiB），冷启动加载所有 daily + factor 文件。结果含首次数据加载，不区分冷热。

5-stocks MA 的耗时分布大致是：CSV 读取（hqdata 内部）≈ 40 %、Bar 构造 + 撮合 ≈ 30 %、CSV 写出 + summary 序列化 ≈ 30 %。这一拆分仍有优化空间（参见 §5）。

代码入口：`tests/integration/test_real_data_ma_5_symbols.py`（需 `~/.hqdata/tushare` 可读否则 skip）；冒烟测试 `tests/data/test_data_layer_performance.py`。

## 4. 性能冒烟测试

`tests/data/test_data_layer_performance.py`：

- `test_daily_hqdata_call_at_most_once_per_date` —— 模拟 `hqdata.api.get_stock_daily_bar` 计数,验证 daily cache 工作(每日期 1 次)。
- `test_factor_hqdata_call_at_most_once_per_date` —— 同上,验证 factor cache 工作。
- `test_bar_objects_reused_across_overlapping_queries` —— `is` 比较同 Bar 实例。
- `test_perf_smoke_50_symbols_250_days_history` —— 50 symbols × 250 days 全量 `history(bar_count=20)` 在 15 秒阈值内完成。
- `test_snapshot_file_missing_propagates_through_cumulative_cache` —— 整日缺失 → `SnapshotFileMissingError` 必须传染,不可静默。
- `test_history_does_not_rescan_full_pre_start_window` —— `DataView.history` 不得触发 `19000101→D` 的全集回扫。

## 5. 调优建议

- **尽早 `set_universe`** —— 在 `initialize` 中限定 symbols，`_symbol_bars` 只对触及的 symbol 累积。
- **`history` 单次取够窗口** —— 一次性取 20/60/120 日窗口，避免多次 5 日短期窗口来回 dispatch。
- **避免在策略中保存 `Bar` 列表** —— 直接依赖 `data.history()` 的返回值，让累积缓存复用。
- **大区间 + 全 universe** —— >10000 日 + 5000 symbols 时考虑 `data_root` 在 SSD 上；CSV 解析是单线程 IO 瓶颈，hqdata 端 `pd.read_csv` 是 dominant cost。
- **CSV 数值列已 dtype=str** —— hqdata 端 stock_daily / stock_factor 的数值列读为 `str` 后再转 `Decimal`,不经过 float64 桥,可避免 `1.123456789123456789` 类长尾精度损失。

## 6. 不属于性能范围

- **网络数据获取**：`hqbacktest` 不调用任何数据源 SDK、不联网；CSV 是 `hqdata` CLI 预落盘的。
- **因子预计算**：复权因子的全市场预计算由 `hqdata` 完成，不在回测运行时发生。
- **多进程 / NUMA 优化**：v0.1 单线程；现测基准显示单线程 IO 已足够，不引入并行复杂度。
- **hqdata 端 calendar.csv 每次 `get_stock_daily_bar` 重读**：见 §1.3 提示，是已知 design tradeoff，不在 hqbacktest 端能进一步优化。
