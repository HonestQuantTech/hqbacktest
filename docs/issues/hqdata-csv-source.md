# feat(sources): 新增 CsvSource —— 让 hqbacktest 通过 hqdata API 读取 CSV snapshot

## 动机

hqdata CLI 已经把日线 / 因子 / 股票池落盘到 `{data_root}/{source}/.../{YYYYMMDD}.csv`。目前 `hqbacktest` 自己再用 `pd.read_csv` 重新解析一遍，存在职责重叠。改造方向：

- hqdata 新增 `CsvSource`，通过 `hqdata.init_source("csv")`（默认 `~/.hqdata/tushare`）或 `hqdata.init_source("csv", root=<path>)`（自定义路径）启用，从快照目录读取并以 `pandas.DataFrame` 形式返回。
- hqbacktest 在回测期调用 `hqdata.api.get_*` 系列接口；CSV 列映射、文件存在性检查、错误分类全部由 hqdata 负责。
- hqbacktest 仍保留自己的双层缓存与 `DataFrame → Bar/Factor` 转换（属于回测侧语义，不在本 issue 范围）。

本 issue 只覆盖 hqdata 这一侧。hqbacktest 侧由另一 issue 跟进（见文末关联）。

## 范围

### 在本 issue 内

- 新增 `hqdata.errors` 模块：`HQDataError`（基类）、`SnapshotFileMissingError`（含 `kind / date / path / source_name` 字段）、`InvalidDataError`（列名 / 数据格式异常）。
- 新增 `hqdata.sources.csv_source.CsvSource(BaseSource)`，实现 `get_calendar` / `get_stock_list` / `get_stock_daily_bar` / `get_stock_factor` / `get_stock_snapshot`。
- `hqdata.api.init_source` 扩展 `"csv"` 分支：kwargs 接受可选 `root`（默认 `~/.hqdata/tushare`，支持 `~` 展开）和可选 `source_name`。
- `TradingCalendar` 与现有 source 一致，loading 失败时 `_source = CsvSource()` 仍可成功（见 §「语义边界」）。
- 单元测试 `tests/test_csv_source.py`（顶层，与 `test_tushare.py` 平级）+ `tests/test_init_source.py::TestInitSourceCsv`。

### 不在本 issue 内

- 不改动 tushare / ricequant / akshare 任何实现。
- 不改动 `hqdata.cli` 与 `compare_cli`（下载 / 比较仍走原逻辑）。
- 不引入 hqbacktest 依赖。
- 不实现 `get_stock_snapshot`（CsvSource 没有实时数据，保留 `NotImplementedError`）。
- 不暴露写 CSV 的反向接口。
- 不在 `hqdata/sources/__init__.py` 显式导出 `CsvSource`——与其他 source 一致地走 lazy import。

## 设计要点

### 数据布局（与 hqdata CLI 落盘一致）

```
<root>/
├── calendar.csv                              # date, is_open ("Y"/"N")
├── stock_list/{YYYYMMDD}.csv                 # symbol, date, name, exchange, board, curr_type, list_date, delist_date
├── stock_daily/{YYYYMMDD}.csv                # symbol, date, pre_close, open, high, low, close, volume, turnover, change, pct_change
└── stock_factor/{YYYYMMDD}.csv               # symbol, date, factor
```

### `CsvSource` API

```python
class CsvSource(BaseSource):
    def __init__(
        self,
        root: Optional[str | Path] = None,   # default: ~/.hqdata/tushare
        source_name: Optional[str] = None,
    ): ...
```

构造期行为：
- `root` 缺省 → `Path.home() / ".hqdata" / "tushare"`（对齐 hqdata CLI 的默认落盘布局）。
- `~` 自动展开。
- 缺失路径**不报错**——让首次真正访问文件时由 `SnapshotFileMissingError` 提示。
- 路径存在但不是目录 → `ValueError`（典型配错：传了一个 `.csv` 当 root）。

### 各接口语义

| 接口 | 行为 | 文件缺失 |
| --- | --- | --- |
| `get_calendar(start, end, is_open)` | 读 `calendar.csv`，过滤 `[start, end]` 与 `is_open`，按 `date` 升序 | `calendar.csv` 缺失 → **返回空 DataFrame**（对齐 tushare/ricequant：calendar 是元数据，不在 critical path 上）。让 `init_source("csv")` 在用户首次安装、未跑 `hqdata` CLI 时仍可成功 |
| `get_stock_list(trade_date, ...)` | 读 `stock_list/{trade_date}.csv`，按 symbol/exchange/board 过滤 | 文件不存在 → `SnapshotFileMissingError("stock_list", trade_date, path)` |
| `get_stock_daily_bar(symbol, start, end, trading_days)` | 用 `get_calendar` 解出实际交易日，逐日读 `stock_daily/{date}.csv`，按 symbol 过滤行，concat 为一张 DataFrame | 单日文件缺失 → `SnapshotFileMissingError("stock_daily", date, path)` |
| `get_stock_factor(trade_date, symbol)` | 读 `stock_factor/{trade_date}.csv`，按 symbol 过滤 | 文件不存在 → `SnapshotFileMissingError("stock_factor", trade_date, path)` |
| `get_stock_snapshot(symbol)` | 不实现 | — |

**个股缺失**：仅是该 symbol 在该日无行 → 静默不返回（与 tushare/ricequant 行为一致）。

**列名一致性**：返回 DataFrame 的列名与 `BaseSource._empty_stock_*` 列名完全对齐；列名漂移在每次读文件时校验，不一致抛 `InvalidDataError`。

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
        # No required kwargs: CsvSource falls back to the default root.
        from hqdata.sources.csv_source import CsvSource
        _source = CsvSource(**kwargs)
```

`tushare` / `ricequant` / `akshare` 分支一字不动。

### 数值类型

CSV 是文本；`pd.read_csv` 默认把数值列推断为 `float64`、日期列推断为 `str`。CsvSource 不在内部做 `Decimal` 转换，**保留 pandas 默认类型**：

- 价格 / `pct_change` 等：`float64`
- `volume`：`int64`（与 tushare 对齐）
- `factor`：`float64`（hqbacktest 在 DataFrame → Factor 转换时按需 `Decimal(str(...))`）
- `date`：始终 `str`（YYYYMMDD）
- `is_open`：`"Y"` / `"N"` 字符串（与 tushare / ricequant / akshare 一致）

为什么不在 CsvSource 内做精度转换？精度是回测侧的账本约束，CsvSource 只做"读 + 过滤"，调用方负责按目标类型转换。

## 实现清单

- [ ] `hqdata/errors.py`（新增）：`HQDataError` 基类 + `SnapshotFileMissingError`（字段 `kind / date / path / source_name`）+ `InvalidDataError`（结构 / 列名异常）
- [ ] `hqdata/sources/csv_source.py`（新增）：`CsvSource`，按上表实现 5 个接口
- [ ] `hqdata/api.py::init_source`：扩展 `Literal` 与 csv 分支（**移除** root 必填校验）
- [ ] `hqdata/tests/test_csv_source.py`（顶层，与现有 `test_tushare.py` 平级）：
    - 构造：默认 `~/.hqdata/tushare`、缺失不报错、`~` 展开、非目录报错、自定义 `source_name`
    - calendar：完整区间 + `is_open` 过滤 + 空区间 + `calendar.csv` 缺失返回空 + 默认根缺失时同样返回空
    - `stock_list`：必填列 + 过滤（symbol / exchange / board）+ 缺文件抛 `SnapshotFileMissingError` + 非法日期校验
    - `stock_daily_bar`：单 symbol / 多 symbol / 多日拼接 + 个股缺行静默 + 单日缺文件抛错 + 列名漂移抛 `InvalidDataError` + `trading_days=0/None` 空结果
    - `stock_factor`：单 symbol / 多 symbol / 缺文件抛错 + 因子值原样透传（ex-dividend day 由 hqbacktest 解释）
    - `get_stock_snapshot`：`NotImplementedError`
- [ ] `hqdata/tests/test_init_source.py`：`init_source("csv")` 路由 + 不传 root 走默认 + 缺 env 错误信息 + 顶层 api 调用抛错（无 source）

## 验收标准

1. `pytest hqdata/tests/ -v` 全绿（104 既有 + 38 新增 = ~142 项）。
2. 编程接口：
   ```python
   hqdata.init_source("csv")                         # 默认 ~/.hqdata/tushare
   hqdata.init_source("csv", root="/path/to/snap")   # 显式路径
   hqdata.init_source("csv", root="~/snap")          # ~ 展开
   hqdata.get_stock_list("20240102")                 # 返回正确 DataFrame
   hqdata.get_stock_daily_bar("600000.SH", "20240102", "20240110")
   hqdata.get_stock_factor("20240102")               # 返回 factor DataFrame
   ```
   全部按 §「各接口语义」表行为。
3. **缺失语义符合下表**：
    - `calendar.csv` 缺失 → 返回空 DataFrame
    - `stock_list/{date}.csv` 缺失 → `SnapshotFileMissingError("stock_list", date, path)`
    - `stock_daily/{date}.csv` 缺失 → `SnapshotFileMissingError("stock_daily", date, path)`
    - `stock_factor/{date}.csv` 缺失 → `SnapshotFileMissingError("stock_factor", date, path)`
    - 列名漂移 → `InvalidDataError`
4. `tushare` / `ricequant` / `akshare` 三条路径行为完全不变（既有测试不退化）。
5. `init_source("csv")`（不传 root）在 `~/.hqdata/tushare` 不存在时**不抛错**——构造 + 首次 `get_calendar` 都返回空；用户真正读到日级数据时才有清晰的错。
6. 整日 snapshot 文件缺失抛 `SnapshotFileMissingError`（**同时继承** `FileNotFoundError` 和 `HQDataError`），既有 `except FileNotFoundError` 代码不破；同时支持 `except SnapshotFileMissingError` 精确分类。

## 风险与注意

- **列名漂移检测时机**：每次 `pd.read_csv` 后立即校验 `expected_columns ⊆ df.columns`；不一致抛 `InvalidDataError`。不预加载所有文件做"启动期 schema 检查"，因为 snapshot 可能只有部分 family。
- **大文件**：单日 `stock_daily` 实测约 5000 行 × ~12 列，`pd.read_csv` 单次无压力；不在 CsvSource 内部加缓存——缓存属于调用方语义，hqbacktest 自己有更严的双层缓存。
- **路径分隔符**：用 `pathlib.Path` 与 `Path.home() / ".hqdata" / "tushare"` 拼接，不手拼字符串。
- **`get_calendar` 缺失语义对齐**：calendar 是元数据（"今天是不是交易日"），用着的人少、缺失多半是"还没下载数据"，所以选"返回空"。股票池 / 日线 / 因子一旦缺失就会直接掐断回测对账，必须中断，所以选"抛异常"。

## 不在 scope 内（提示但不实现）

- hqbacktest 改造（`HqDataCsvPortal` → 调用 hqdata API；`DataFrame → Bar` 转换；双层缓存）—— 见 `TODO.md` §3 阶段 B。
- hqdata CsvSource 的内部缓存（理由：缓存属于调用方语义，hqbacktest 自己有更严的双层缓存）。
- hqdata CsvSource 的写接口（CSV 落盘仍走原 `hqdata.cli`）。

## 关联

- 实施计划根文档：`hqbacktest/TODO.md` §3「实施阶段」阶段 A
- 上下文：hqbacktest issue（待开）——「feat(portal): 改为通过 hqdata API 读取 CSV」
- 设计依据：`hqbacktest/docs/design/mvp-contract.md` §3.1「数据边界」（改造后该节措辞需更新，由 hqbacktest 侧 issue 一并处理）
