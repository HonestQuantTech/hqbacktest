# feat(sources): 新增 CsvSource —— 让 hqbacktest 通过 hqdata API 读取 CSV snapshot

## 动机

hqdata CLI 已经把日线 / 因子 / 股票池落盘到 `{data_root}/{source}/.../{YYYYMMDD}.csv`。目前 `hqbacktest` 自己再用 `pd.read_csv` 重新解析一遍，存在职责重叠。改造方向：

- hqdata 新增 `CsvSource`，通过 `hqdata.init_source("csv", root=..., source_name=...)` 启用，从快照目录读取并以 `pandas.DataFrame` 形式返回。
- hqbacktest 在回测期调用 `hqdata.api.get_*` 系列接口；CSV 列映射、文件存在性检查、错误分类全部由 hqdata 负责。
- hqbacktest 仍保留自己的双层缓存与 `DataFrame → Bar/Factor` 转换（属于回测侧语义，不在本 issue 范围）。

本 issue 只覆盖 hqdata 这一侧。hqbacktest 侧由另一 issue 跟进（见文末关联）。

## 范围

### 在本 issue 内

- 新增 `hqdata.errors.SnapshotFileMissingError`（含 `kind` / `date` / `path` / `source_name` 字段）。
- 新增 `hqdata.sources.csv_source.CsvSource(BaseSource)`，实现 `get_calendar` / `get_stock_list` / `get_stock_daily_bar` / `get_stock_factor` / `get_stock_snapshot`。
- `hqdata.api.init_source` 扩展 `"csv"` 分支：从 kwargs 取 `root`，构造 `CsvSource`。
- 单元测试 `tests/sources/test_csv_source.py` + `tests/api/test_init_source.py::test_init_source_csv`。

### 不在本 issue 内

- 不改动 tushare / ricequant / akshare 任何实现。
- 不改动 `hqdata.cli` 与 `compare_cli`（下载 / 比较仍走原逻辑）。
- 不引入 hqbacktest 依赖。
- 不实现 `get_stock_snapshot`（CsvSource 没有实时数据，保留 `NotImplementedError`）。
- 不暴露写 CSV 的反向接口。

## 设计要点

### 数据布局（与 hqdata CLI 落盘一致）

```
<root>/
├── calendar.csv                              # date, is_open
├── stock_list/{YYYYMMDD}.csv                 # symbol, date, name, exchange, board, curr_type, list_date, delist_date
├── stock_daily/{YYYYMMDD}.csv                # symbol, date, pre_close, open, high, low, close, volume, turnover, change, pct_change
└── stock_factor/{YYYYMMDD}.csv               # symbol, date, factor
```

### `CsvSource` API

```python
class CsvSource(BaseSource):
    def __init__(self, root: str | Path, source_name: str = "csv"):
        ...
```

构造期做最少校验：`root` 存在且是目录；否则 `ValueError`。**不**要求 `calendar.csv` 当时就存在（部分回测可能只跑子集）。

### 各接口语义

| 接口 | 行为 | 整日文件缺失 |
| --- | --- | --- |
| `get_calendar(start, end, is_open)` | 读 `calendar.csv`，过滤 `[start, end]` 与 `is_open` | 文件不存在 → `SnapshotFileMissingError("calendar", "", path)`；返回空 DataFrame |
| `get_stock_list(trade_date, ...)` | 读 `stock_list/{trade_date}.csv`，按 symbol/exchange/board 过滤 | 文件不存在 → `SnapshotFileMissingError("stock_list", trade_date, path)` |
| `get_stock_daily_bar(symbol, start, end, trading_days)` | 用 `get_calendar` 解出实际交易日，逐日读 `stock_daily/{date}.csv`，按 symbol 过滤行，concat 为一张 DataFrame | 单日文件缺失 → `SnapshotFileMissingError("stock_daily", date, path)` |
| `get_stock_factor(trade_date, symbol)` | 读 `stock_factor/{trade_date}.csv`，按 symbol 过滤 | 文件不存在 → `SnapshotFileMissingError("stock_factor", trade_date, path)` |
| `get_stock_snapshot(symbol)` | 不实现 | — |

**个股缺失**：仅是该 symbol 在该日无行 → 静默不返回（与 tushare/ricequant 行为一致）。

**列名一致性**：返回 DataFrame 的列名与 `BaseSource._empty_stock_*` 列名完全对齐，便于 hqbacktest 转 Bar。

### `init_source` 签名

现状：

```python
def init_source(source_type: Literal["ricequant", "tushare", "akshare"], **kwargs) -> None
```

扩展后：

```python
def init_source(
    source_type: Literal["ricequant", "tushare", "akshare", "csv"], **kwargs
) -> None:
    ...
    elif source_type == "csv":
        if "root" not in kwargs:
            raise ValueError("init_source('csv', ...) requires root=<path>")
        from hqdata.sources.csv_source import CsvSource
        _source = CsvSource(**kwargs)
```

`tushare` / `ricequant` / `akshare` 分支一字不动。

### 数值类型

CSV 是文本；`factor` / `close` / `volume` / `amount` 等列读出后用 `Decimal(str(...))` 转，**禁止** `Decimal(float(...))`（与 hqbacktest 已有约定一致）。`is_open` 视为 `int` 0/1；`date` 保持 `str`（YYYYMMDD）。

## 实现清单

- [ ] `hqdata/errors.py`（若已有则同文件）：新增 `SnapshotFileMissingError`，字段 `kind / date / path / source_name`
- [ ] `hqdata/sources/csv_source.py`：新增 `CsvSource`，按上表实现 5 个接口
- [ ] `hqdata/sources/__init__.py`：导出 `CsvSource`
- [ ] `hqdata/api.py::init_source`：扩展 `Literal` 与 csv 分支
- [ ] `hqdata/tests/sources/test_csv_source.py`：
    - `calendar.csv` 读取 + `is_open` 过滤 + 空区间返回
    - `stock_list/{d}.csv` 正常 + 缺文件抛 `SnapshotFileMissingError`
    - `stock_daily/{d}.csv` 跨区间 union + symbol 过滤 + 个股缺行静默 + 单日缺文件抛异常
    - `stock_factor/{d}.csv` 正常
    - 列名与 `BaseSource._empty_stock_*` 一致
- [ ] `hqdata/tests/api/test_init_source.py`：加 `test_init_source_csv` 用 `tmp_path` 伪造 `{root}` 后调用 `hqdata.api.*` 验证接口可用

## 验收标准

1. `pytest hqdata/tests/ -v` 全绿。
2. 新增一行：

   ```python
   init_source("csv", root="/path/to/snapshot")
   hqdata.get_stock_list("20240102")   # 返回正确 DataFrame
   hqdata.get_stock_daily_bar("600000.SH", "20240102", "20240110")  # 跨日 concat 正确
   hqdata.get_stock_factor("20240102")  # 返回 factor DataFrame
   ```

   全部按 §「各接口语义」表行为。
3. 缺文件时抛 `SnapshotFileMissingError`（含明确 `kind / date / path / source_name`），不抛裸 `FileNotFoundError`，不静默返回空。
4. `tushare` / `ricequant` / `akshare` 三条路径行为完全不变（既有测试不退化）。
5. `hqdata.api.init_source("csv")` 缺 `root=` 报清晰错误，不静默使用空字符串。

## 风险与注意

- **列名漂移**：`CsvSource` 构造后第一次调用任何接口前，先做一次列名校验（用 `_empty_stock_*` 的列集合 ⊆ 实际读出 csv 列集合），不一致直接抛 `InvalidDataError` 形异常。
- **大文件**：单日 `stock_daily` 实测约 5000 行 × ~12 列，`pd.read_csv` 单次无压力；不在本 issue 引入缓存（缓存属于 hqbacktest 侧，见另一 issue）。
- **路径分隔符**：用 `pathlib.Path`，不手拼字符串。
- **类型转换**：`pd.read_csv` 默认数值列读成 `float64`；CsvSource 内对 `factor` / 价格 / 数量列做显式 `Decimal(str(s))` 转换。

## 不在 scope 内（提示但不实现）

- hqbacktest 改造（`HqDataCsvPortal` → 调用 hqdata API；`DataFrame → Bar` 转换；双层缓存）—— 见 TODO.md §3 阶段 B。
- hqdata CsvSource 的内部缓存（理由：缓存属于调用方语义，hqbacktest 自己有更严的双层缓存）。
- hqdata CsvSource 的写接口（CSV 落盘仍走原 `hqdata.cli`）。

## 关联

- 实施计划根文档：`hqbacktest/TODO.md` §3「实施阶段」阶段 A
- 上下文：hqbacktest issue（待开）—— 「feat(portal): 改为通过 hqdata API 读取 CSV」
- 设计依据：`hqbacktest/docs/design/mvp-contract.md` §3.1「数据边界」（改造后该节措辞需更新，由 hqbacktest 侧 issue 一并处理）
