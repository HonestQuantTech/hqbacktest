# Changelog

All notable changes to `hqbacktest` are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-08-25

Patch release that hardens `hqbacktest` against the v0.1 real-data
review (see [TODO.md](./TODO.md)「v0.1 评审结论」). All changes are
backward-compatible unless called out below.

### Added
- **Data layer hardening (task 14):** `get_bars` / `get_factor` allow
  per-day gaps in the window; `SnapshotFileMissingError` (subclass of
  `MissingDataError`) distinguishes a missing whole-day snapshot from
  a per-symbol gap. `current_price` walks back up to 20 trading days
  for the most recent valid close. The first-trading-day sentinel
  `visible_through="00000000"` no longer raises; `history` returns
  `[]` and `current_price` returns `None`. `InMemoryDataPortal`
  drops its forward-walk universe fallback to match the CSV portal's
  per-date semantics. `.BJ` symbols are excluded by default
  (`include_bj=True` opt-in). `Bar.volume` is documented as **手**.
  Defensive copies returned from cached lists.
- **Data layer performance (task 15):** per-day file cache
  (`{date: {symbol: Bar}}`) plus per-symbol cumulative sequences
  (`_symbol_bars[symbol]`) with `bisect` slicing. The 5-symbol
  moving-average strategy over the v0.1.1-calibrated 139-day window
  finishes well under the 60-second budget.
- **Match & ledger semantics (task 16):** `SimulatedBroker.match`
  matches all SELL orders first, then BUYs, with rolling cash so
  same-day "卖旧买新" rotations are not falsely rejected for cash.
  SELL orders are no longer lot-rounded; `order_target(symbol, 0)`
  can flatten a position that contains odd-lot shares. BUY-only lot
  rounding is preserved. 7 contract-level invariants are pinned
  in `docs/design/mvp-contract.md` §3.4 (T+1 whole-order rejection,
  `realized_pnl` excludes fees, `ROUND_HALF_EVEN`, etc.).
- **Equity curve & metrics baseline (task 17):** first-day P&L now
  flows into `daily_return` and `drawdown` (anchored to
  `initial_cash`), so the chained-product identity `∏(1+daily_return)
  == 1+total_return` holds for any run length. `daily_volatility`
  returns `None` for runs with fewer than 2 daily returns (no more
  misleading 0 / `nan`). `metrics.py` rebuilds `Decimal` via
  `Decimal(str(...))` to avoid `Decimal(float)` artifacts.
- **Strategy isolation & audit trail (task 18):** `Order` is now
  `@dataclass(frozen=True)` with `fill_ids: tuple[str, ...]`; strategies
  cannot mutate Order objects returned from `Context.pending_orders()`.
  `DataView.portal` is now a private `_portal` field; strategies cannot
  bypass `visible_through`. `set_universe(...)` enforces trading scope;
  orders outside the universe are rejected with
  `RejectReason.OUT_OF_UNIVERSE`. New `Context.historical_universe()`
  returns the historical stock list through the guarded data view.
- **Factor diagnostics on holdings (task 19):** the engine runs
  `analyze_factor_series` against holdings-period factor series
  with a 0.1% relative jump band. Any holding-period factor jump emits
  a `DATA_WARNING` event and a `FactorDiagnostic` entry; the
  diagnostics are observability-only — cash, position and equity are
  byte-identical with the no-diagnostics baseline. CLI prints a one-line
  summary at run end when diagnostics fired. **`adjustment_policy=none`
  still excludes dividends from the NAV** (contract task 9 invariant);
  the diagnostics surface this bias; long-window NAV remains
  unsuitable for return estimation.
- **CLI first-mile + documentation honesty (task 20):**
  `hqbacktest run` (the console script) prepends the config file's
  directory and the current working directory to `sys.path` so the
  strategy module can be resolved by name alone (matching
  `python -m hqbacktest run`). Config validation rejects `nan` /
  `inf` / float `initial_cash`, impossible calendar dates, and
  empty trading-day windows with single-line `ConfigError` (CLI exit 2).
  Output directories that already contain prior-run files are rejected
  with exit 3; `--force` overrides. `Context.order_value` accepts
  `int` / `str` cash amounts. `run_metadata.json`'s `git_commit` now
  records the hqbacktest package's own commit (not the user's cwd).
  README "项目状态" / "命令行" / "错误信息" / "包布局" sections
  brought into line with the implementation. `BaseStrategy.__init__`
  accepts and stores `**kwargs` so `[strategy].kwargs` round-trips.

### Added (test infrastructure)
- **`tests/integration/`** (task 21): four real-data smoke scenarios
  against `~/.hqdata/tushare`, auto-skipped when the snapshot is
  missing (no credentials, no network):
  1. buy_and_hold across 600000.SH's 2026-07-16 dividend ex-date — the
     task-14/19 contract (`factor jump 16.5935 → 17.3774` produces a
     `DATA_WARNING`) is enforced end-to-end.
  2. 5-symbol moving-average strategy over the full 139-day window —
     byte-deterministic across two runs and below the 60-second budget.
  3. universe containing the known suspended symbol `000008.SZ`
     (suspended 2026-07-07..2026-07-13) — no crash, the fallback-close
     valuation `DATA_WARNING` is recorded.
  4. first-trading-day `before_trading_start` reads `current_price` —
     returns `None` against the sentinel without crashing.

### Constraints (unchanged from v0.1)
- `adjustment_policy` MUST be `"none"`.
- Market orders only; limit / stop / partial fills raise
  `UnsupportedOrderTypeError` / are rejected.
- Only Chinese A-share common stocks (沪深); no ST / 涨跌停 / 新股 /
  北交所 / 融资融券 / 期权 support.
- Default A-share cost model only.
- CSV-only data ingestion via hqdata CLI; no network calls; no tokens
  read or written.

### Known limitations carried forward
- `adjustment_policy=none` means the NAV systematically underestimates
  cross-ex-date windows (no dividend accounting). Task-19 factor
  diagnostics surface the jumps but do not fabricate dividends.
- `Position.update_buy` uses simple-average cost (not FIFO).
- `BacktestResult.metrics` reconstruction on `load()` is best-effort.
- README's "路线图" section lists capabilities deferred to v0.2+.

## [0.1.0] - 2026-08-23

First public release of `hqbacktest`. The project implements tasks 1-13 of
[TODO.md](./TODO.md).

### Added

- **Domain** (task 3): `Order` / `Fill` / `Position` / `Portfolio` /
  `AccountSnapshot` / `CorporateActionAdjustment` dataclasses, order state
  machine with `ACCEPTED` → `PENDING` → `FILLED` (or `REJECTED` /
  `CANCELLED`), `Decimal` precision helpers with `ROUND_HALF_EVEN`.
- **Data** (task 4): `MarketDataPortal` Protocol with two implementations:
  - `HqDataCsvPortal` reads only CSV files dropped by the hqdata CLI at
    `{data_root}/{source}/`; no network, no SDK imports.
  - `InMemoryDataPortal` for unit tests and example scripts.
  `DataView` enforces per-phase `visible_through` cutoffs (contract §4).
- **Engine** (tasks 5-10): five-phase event clock
  (`SESSION_START` → `BEFORE_TRADING_START` → `OPEN_MATCH` → `BAR_CLOSE` →
  `AFTER_TRADING_END`); `SimulatedBroker` for OPEN_MATCH market orders;
  `TradingRuleSet` with six default v0.1 rules (`LongOnly`, `LotSize`,
  `NonTradingDay`, `InvalidPrice`, `InsufficientCash`, `T1Sellable`);
  `CostModel` with explicit A-share fees (0.025% commission + 5 CNY floor,
  0.1% stamp tax on SELL, 0 transfer fee); `BaseStrategy` lifecycle with
  read-only `Context`; `BacktestResult` exposing `equity_curve`,
  `orders_table`, `fills_table`, `positions_table`, `costs_table`,
  `PerformanceMetrics`, `events.jsonl`, `data_version`,
  `factor_diagnostics`; `save(dir)` / `load(dir)` round-trip.
- **CLI** (task 12): `hqbacktest run --config FILE --output DIR` with TOML
  schema, validation, and per-run output directory containing
  `config.toml`, `run_metadata.json`, `events.jsonl`, the five CSVs and
  `summary.json`. Never writes tokens, environment variables, or absolute
  local paths to the output.
- **Examples** (task 11): `examples/buy_and_hold.py` and
  `examples/moving_average.py` demonstrate the full public API on a 7-day
  `InMemoryDataPortal` fixture.
- **Continuous integration & release** (task 13): `.github/workflows/ci.yml`
  runs on Python 3.10/3.11/3.12 and enforces `black --check`, `pytest`
  (with a ≥80% branch-coverage gate via `pytest-cov`), the end-to-end
  examples, a CLI smoke run, and `python -m build` (sdist + wheel).

### Constraints (v0.1)

- `adjustment_policy` MUST be `"none"`. `factor_total_return` and any
  other value are rejected at config validation with a clear reason.
- Market orders only; limit/stop orders raise `UnsupportedOrderTypeError`.
- Only Chinese A-share common stocks (沪深); no ST / 涨跌停 / 新股 /
  北交所 / 融资融券 / 期权 support.
- Default cost model charges A-share v0.1 fees; no per-account overrides.
- CSV-only data ingestion (via hqdata CLI). hqbacktest never makes network
  calls and never reads tokens.

### Known limitations (planned for v0.2+)

- `Position.update_buy` uses simple-average cost (not FIFO); `win_rate` is
  approximate by design.
- `BacktestResult.metrics` is rebuilt on `load()` only as a JSON dict;
  typed `PerformanceMetrics` reconstruction is best-effort.
- The `rule_set` field is stripped from `summary.json` to keep the file
  byte-stable across runs; the live engine retains the full set.
- README's §路线图 section lists capabilities deferred to v0.2+.

[0.1.1]: https://github.com/HonestQuantTech/hqbacktest/releases/tag/v0.1.1
[0.1.0]: https://github.com/HonestQuantTech/hqbacktest/releases/tag/v0.1.0
