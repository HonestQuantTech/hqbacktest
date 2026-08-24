# Changelog

All notable changes to `hqbacktest` are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

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

[0.1.0]: https://github.com/HonestQuantTech/hqbacktest/releases/tag/v0.1.0
