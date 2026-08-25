"""Real-data integration smoke tests.

These tests run against the local `~/.hqdata/tushare` snapshot only when
that directory exists AND contains the calendar.csv + a non-empty
stock_daily subfolder. On machines without the snapshot the entire
group is skipped (no credentials, no network). They live under
`tests/integration/` so the default `pytest tests/` run can opt out
via the `tests` testpath.

Calibration snapshot: `~/.hqdata/tushare`, 2026-01-05 .. 2026-07-31
(139 trading days, ~5200 symbols). Re-calibrate the assertions if the
local snapshot changes.

The four scenarios cover the v0.1 findings that previously broke
real-data runs:

    1. buy_and_hold across 600000.SH's 2026-07-16 dividend ex-date:
       factor jumps 16.5935 -> 17.3774 (~4.7%), factor diagnostics
       warn on the holding-period jump.
    2. 5-symbol moving-average strategy over the full window:
       must complete and be byte-deterministic across two runs.
    3. Universe that contains a known suspended symbol (000008.SZ,
       suspended 2026-07-07..2026-07-13): no crash, warnings traceable.
    4. First-trading-day `before_trading_start` reads `current_price`:
       returns None (sentinel visible_through) without crashing.
"""
