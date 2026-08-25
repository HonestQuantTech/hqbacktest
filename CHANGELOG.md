# Changelog

All notable changes to `hqbacktest` are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.4] - 2026-08-25

Documentation-and-test-coverage hotfix for v0.1.3 (review findings
addressed as task 24 in [TODO.md](./TODO.md)). All changes are
backward-compatible unless called out below. No public API
changes; this release closes documentation/test gaps and aligns
comments with Python language semantics.

### Fixed
- **`get_factor` parity (task 24.1).**
  `tests/data/test_portal_parity.py` previously asserted only that
  the memory portal rejected zero-valued factors; the CSV portal's
  equivalent behaviour lived in a separate file, and no test ever
  ran the same fixture through both portals to assert identical
  `(date, factor)` tuples. Six new parity tests now cover the full
  `get_factor` contract: window returns identical series, per-symbol
  factor gap matches, empty-when-never-listed matches, window
  `start > end` raises `InvalidDataError` in both, bad symbol
  rejected in both, and `SnapshotFileMissingError` vs per-symbol gap
  remains distinguishable. The memory fixture now carries factor
  rows that mirror the new CSV fixture (600000.SH on every trading
  day, 000001.SZ on 20240102 and 20240105 only).
- **`get_bar(symbol, date)` reference removed (task 24.2).**
  `docs/design/mvp-contract.md` §3.3 referenced a `get_bar(symbol,
  date)` single-point query method that has never existed on
  `MarketDataPortal` (only `get_bars(symbol, start, end)` and
  `current_price(symbol)`). The failure classification rows for
  "individual-day missing vs whole-day snapshot missing" are real
  and correct, but belong to `get_bars` / `current_price` — they
  have been merged into a single `get_bars` / `get_factor` failure
  classification row that explicitly notes the protocol has no
  `get_bar` method.
- **`DataView.portal` privacy wording (task 24.3).**
  Both `data_view.py` (class docstring + `__init__` comment) and
  `mvp-contract.md` §3.6 used phrasing that implied the
  `_portal` underscore was a language-level guarantee ("strategies
  cannot reach the raw portal"). This was inaccurate — Python has
  no language-level private attributes; a sufficiently determined
  caller can still reach `_portal` directly. The comments now
  describe the leading-underscore as a strong social convention and
  acknowledge the Python limitation honestly, while keeping the
  `AttributeError` on the public name and the recommendation to go
  through `history` / `current_price` / `universe`.

### Added (test infrastructure)
- **`tests/data/test_portal_parity.py`:** 6 new parity tests for
  `get_factor` (see above). Total parity coverage now spans calendar,
  universe, bars, **and** factor — no "API claimed parity but missing"
  gap.

### Internal cleanup (closing v0.1.4's "v0.1.4 之后可考虑" item)
- **Sentinel constant convergence.** The `"00000000"` "no history"
  sentinel was previously defined as three differently-named constants
  in three places (`data.data_view.NO_HISTORY_SENTINEL`,
  `data.validators.SENTINEL_NO_HISTORY`,
  `engine.scheduler.NO_HISTORY_VISIBLE_THROUGH`). They are now
  collapsed to a single definition (`data.validators.SENTINEL_NO_HISTORY`)
  re-exported through `hqbacktest.data.SENTINEL_NO_HISTORY`. The other
  two call sites import that name directly. Single source of truth;
  no behavioral change.
- **`test_version_matches_pyproject` hardening.** Beyond the
  byte-equality check, the test now also guards two release-time footguns:
  (a) `__version__` / `pyproject.toml [project].version` must not have
  stray whitespace; (b) both values must match the `N.N[.N...]` semver
  shape (rejects e.g. `"v0.1.4"` or an empty placeholder). Reverse
  validation confirmed the test fails on `"  "` (whitespace) and on
  `"v0.1.4"` (semver shape) before the next release.

## [0.1.3] - 2026-08-25

Correctness hotfix for v0.1.2 (review findings addressed as task 23
in [TODO.md](./TODO.md)). All changes are backward-compatible
unless called out below.

### Fixed
- **Volatility / Sharpe now see the first trading day (task 23).**
  `metrics.compute_metrics` previously re-derived the daily-return
  series from `EquityPoint.total_equity` with `[Decimal("0")]` as
  the day-0 seed; the day-0 return was therefore **silently
  dropped** before reaching `stdev`. A 2-day backtest with one real
  day-1 return therefore reported `daily_volatility=None` even
  though `max_drawdown` saw the day-0 loss correctly — a "drawdown
  sees it, volatility doesn't" discrepancy. The fix reads
  `EquityPoint.daily_return` directly (the same value the engine
  already anchored to `initial_cash`, task 17), so the volatility,
  annualised volatility and Sharpe ratio now agree with
  `max_drawdown` on what counts as a "first day". The dead-code
  helper `_drawdown_series` (unused since task 17 wired
  `max_drawdown` straight from `EquityPoint.drawdown`) was also
  deleted so the same zero-seed defect cannot reappear via a
  future accidental caller. The pre-fix regression test
  `test_two_day_volatility_is_none_when_only_one_return` was
  removed: it asserted `None` based on the bug. A new
  hand-calculated regression test (`test_two_day_volatility_uses_both_daily_returns`)
  pins the corrected behaviour (2 days at -9% / +5.5% ->
  `daily_volatility` ≈ 0.10253).

## [0.1.2] - 2026-08-25

Documentation-and-correctness hotfix for v0.1.1 (review findings
addressed as task 22 in [TODO.md](./TODO.md)). All changes are
backward-compatible unless called out below.

### Fixed
- **`source` accepts absolute paths (task 22.1).**
  `resolve_source_location` now splits an absolute path
  (e.g. `~/.hqdata/tushare`) into `(parent_dir, basename)` and pairs
  the bare-name form (`"tushare"`) with `[data].data_root` as before.
  Relative paths like `"foo/bar"` are still rejected to avoid
  cwd-relative ambiguity. Behavior of the bare-name form is
  unchanged.
- **`run_metadata.json` no longer leaks absolute local paths
  (task 22.2).** `config_path`, `output_directory`, and
  `config_output_directory` are written as paths relative to the
  run-time cwd (via `os.path.relpath`). Token / env-var / absolute-
  path leak coverage in `test_runner_does_not_write_secrets` was
  extended to assert these field-level invariants; the previous
  assertion only scanned for an explicit token string.
- **Impossible calendar dates are now rejected by
  `validate_yyyymmdd` (task 22.3).** 8-digit strings like `20241399`
  (month 13), `20240230` (Feb 30), `20240132` (Jan 32), `20230229`
  (Feb 29 in a non-leap year) are now caught by the validator using
  `datetime.strptime`. The sentinel `"00000000"` is preserved for
  the first-trading-day case. The prior regression test
  `test_start_date_impossible_rejected` set `end_date` to a
  lex-smaller string so it triggered the `start > end` ordering
  check, not the impossible-date check — that test was a false
  positive and has been rewritten to use a lex-greater real date so
  it exercises the intended failure mode. As a side-effect, the
  task-15 perf fixture generator (which used an integer counter whose
  `d % 100 == 32` skip only caught the day-32 rollover after a 31-day
  month, so it emitted impossible dates like `20240230`) was rewritten
  to iterate via `datetime.timedelta`.
- **Documentation honesty (task 22.4):**
  `pyproject.toml:26` no longer claims "no runtime deps yet" — the
  line was adjacent to the already-declared `pandas` / `tomli`
  dependencies. The CLI test previously named
  `test_console_script_runs_end_to_end` but actually ran via
  `python -m hqbacktest` (its own docstring admitted this); the
  rename to `test_python_m_runs_end_to_end` makes the contract
  obvious, and a new sibling `test_console_script_runs_end_to_end`
  invokes the installed `hqbacktest` console-script binary (skipping
  gracefully on machines where `pip install -e .` has not been run).
  README's "26 项 CLI 测试" call-out was removed in favour of a
  reference to `tests/cli/`, so the count never goes stale.

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
  reach it to read future data. `set_universe(...)` enforces trading
  scope; orders outside the universe are rejected with
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
  `inf` / float `initial_cash` and empty trading-day windows with
  single-line `ConfigError` (CLI exit 2). Impossible calendar dates
  such as `20241399` or `20240230` are now rejected by
  `validate_yyyymmdd` itself, not just by the `start > end` ordering
  check (task 22.3).
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
