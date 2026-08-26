"""Performance and cache-reuse smoke tests for the data layer.

Covers:
    * The per-day CSV files are parsed **at most once** per run; calling
      `get_bars` / `get_factor` repeatedly never re-reads the file.
    * `get_bars` on overlapping windows reuses the cached `Bar` objects
      (no per-call object reconstruction).
    * A 50-symbol × 250-day backtest that calls `data.history(bar_count=20)`
      on every (symbol, day) finishes within a CI-friendly time budget.

Thresholds are deliberately generous so a busy CI runner does not flake.
"""

from __future__ import annotations

import time
from bisect import bisect_left, bisect_right
from decimal import Decimal
from pathlib import Path
from typing import Dict, List

import pytest

from hqbacktest.data import DataView, HqDataCsvPortal
from hqbacktest.data.hqdata_portal import HqDataCsvPortal as _PortalCls


# ---------------------------------------------------------------------------
# Fixture helpers (reused from test_hqdata_portal style)
# ---------------------------------------------------------------------------


def _write_calendar(root: Path, rows: list) -> None:
    lines = ["date,is_open"]
    for d, f in rows:
        lines.append(f"{d},{f}")
    (root / "calendar.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_stock_list(root: Path, date: str, symbols: list) -> None:
    target = root / "stock_list"
    target.mkdir(parents=True, exist_ok=True)
    lines = ["symbol,date,name,exchange,board,curr_type,list_date,delist_date"]
    for sym in symbols:
        lines.append(f"{sym},{date},name,SSE,MB,CNY,19990101,")
    (target / f"{date}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_stock_daily(root: Path, date: str, rows: list) -> None:
    target = root / "stock_daily"
    target.mkdir(parents=True, exist_ok=True)
    fields = [
        "symbol",
        "date",
        "pre_close",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
        "change",
        "pct_change",
    ]
    lines = [",".join(fields)]
    for r in rows:
        lines.append(",".join(str(r[f]) for f in fields))
    (target / f"{date}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_stock_factor(root: Path, date: str, rows: list) -> None:
    target = root / "stock_factor"
    target.mkdir(parents=True, exist_ok=True)
    lines = ["symbol,date,factor"]
    for r in rows:
        lines.append(f"{r['symbol']},{date},{r['factor']}")
    (target / f"{date}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_synthetic_snapshot(
    root: Path,
    *,
    symbols: List[str],
    trading_days: List[str],
    include_factor: bool = True,
) -> Path:
    snap = root / "tushare"
    snap.mkdir(parents=True, exist_ok=True)
    _write_calendar(snap, [(d, "Y") for d in trading_days])
    for d in trading_days:
        _write_stock_list(snap, d, symbols)
        rows = []
        for i, sym in enumerate(symbols):
            close = 10 + (i % 5) + (int(d) % 7) * 0.1
            rows.append(
                {
                    "symbol": sym,
                    "date": d,
                    "pre_close": close - 0.1,
                    "open": close,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 10000,
                    "turnover": 100000,
                    "change": 0.1,
                    "pct_change": 1.0,
                }
            )
        _write_stock_daily(snap, d, rows)
        if include_factor:
            f_rows = [
                {"symbol": sym, "factor": 1.0 + (int(d) % 3) * 0.01} for sym in symbols
            ]
            _write_stock_factor(snap, d, f_rows)
    return snap


# ---------------------------------------------------------------------------
# Cache reuse tests
# ---------------------------------------------------------------------------


def test_daily_hqdata_call_at_most_once_per_date(tmp_path, monkeypatch):
    """The portal must invoke `hqdata.get_stock_daily_bar` at most once per
    trading day, regardless of how many symbols query that day.

    After the v0.1 refactor the portal no longer parses CSV directly —
    it routes through `hqdata.api`. Cache-reuse guarantees are then
    measured at the hqdata API boundary: each `(date)` should be read
    **once** even when ten overlapping `get_bars` queries hit the
    portal.
    """
    import hqdata

    symbols = ["600000.SH", "000001.SZ", "688001.SH"]
    days = ["20240102", "20240103", "20240104"]
    _build_synthetic_snapshot(tmp_path, symbols=symbols, trading_days=days)

    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))

    api_calls: Dict[str, int] = {}

    real_daily = hqdata.get_stock_daily_bar

    def counting_daily(symbol, start_date, end_date, *args, **kwargs):
        key = f"daily|{start_date}|{end_date}"
        api_calls[key] = api_calls.get(key, 0) + 1
        return real_daily(symbol, start_date, end_date, *args, **kwargs)

    monkeypatch.setattr(hqdata, "get_stock_daily_bar", counting_daily)

    # Issue many overlapping queries across symbols and windows.
    for _ in range(5):
        portal.get_bars("600000.SH", "20240102", "20240104")
    for _ in range(5):
        portal.get_bars("000001.SZ", "20240102", "20240103")
    portal.get_bars("688001.SH", "20240104", "20240104")

    # Each unique trading day should be fetched once — the portal's
    # `_read_day_bars(date)` cache feeds every subsequent symbol query
    # from the cached `dict[symbol, Bar]`.
    for d in days:
        key = f"daily|{d}|{d}"
        assert api_calls.get(key, 0) == 1, (
            f"hqdata.get_stock_daily_bar({d},{d}) invoked "
            f"{api_calls.get(key, 0)} times, expected 1"
        )

    # Total calls across all days should be exactly len(days) — no
    # implicit windows (e.g. wider ranges fetching every date again).
    assert sum(api_calls.values()) == len(days)


def test_factor_hqdata_call_at_most_once_per_date(tmp_path, monkeypatch):
    """The portal must invoke the factor API at most once per trading day,
    independent of how many symbols query that day.

    Same cache-reuse contract as for bars: per-day `factor` data is
    parsed once and reused for every symbol's `get_factor` query on that
    day. The portal's `_read_day_factors(date)` cache is responsible.
    """
    import hqdata

    symbols = ["600000.SH", "000001.SZ"]
    days = ["20240102", "20240103"]
    _build_synthetic_snapshot(tmp_path, symbols=symbols, trading_days=days)

    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))

    api_calls: Dict[str, int] = {}
    real_factor = hqdata.get_stock_factor

    def counting_factor(*args, **kwargs):
        # hqdata.get_stock_factor has signature (symbol=None, trade_date=None);
        # normalize on `trade_date` (the value the portal passes).
        trade_date = kwargs.get("trade_date") or (
            args[1] if len(args) >= 2 else args[0] if args else None
        )
        key = f"factor|{trade_date}"
        api_calls[key] = api_calls.get(key, 0) + 1
        return real_factor(*args, **kwargs)

    monkeypatch.setattr(hqdata, "get_stock_factor", counting_factor)

    portal.get_factor("600000.SH", "20240102", "20240103")
    portal.get_factor("000001.SZ", "20240102", "20240103")
    portal.get_factor("600000.SH", "20240102", "20240102")

    for d in days:
        key = f"factor|{d}"
        assert api_calls.get(key, 0) == 1, (
            f"hqdata.get_stock_factor({d}) invoked "
            f"{api_calls.get(key, 0)} times, expected 1"
        )
    assert sum(api_calls.values()) == len(days)


def test_bar_objects_reused_across_overlapping_queries(tmp_path):
    """`get_bars` overlapping windows must return the same `Bar` instances.

    Memory control: a fresh object per call would multiply
    allocation by the number of overlapping queries. Bar objects are
    cached and reused across overlapping queries.
    """
    symbols = ["600000.SH"]
    days = ["20240102", "20240103", "20240104"]
    _build_synthetic_snapshot(tmp_path, symbols=symbols, trading_days=days)

    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))

    wide = portal.get_bars("600000.SH", "20240102", "20240104")
    narrow = portal.get_bars("600000.SH", "20240103", "20240104")
    # The bar at 20240104 is the same object across both queries.
    assert wide[-1] is narrow[-1]
    assert wide[-2] is narrow[-2]


def test_history_does_not_rescan_full_pre_start_window(tmp_path):
    """`DataView.history` must not hit the portal with `19000101→D` windows.

    With the task-15 cache the underlying `get_bars` call should use a
    bounded window (not the legacy 19000101 start). This is a regression
    guard for the original 2026-08 finding.
    """
    symbols = ["600000.SH"]
    days = ["20240102", "20240103", "20240104", "20240105", "20240108"]
    _build_synthetic_snapshot(tmp_path, symbols=symbols, trading_days=days)
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))

    captured: List = []

    real_get_bars = portal.get_bars

    def spy(symbol, start, end):
        captured.append((symbol, start, end))
        return real_get_bars(symbol, start, end)

    portal.get_bars = spy  # type: ignore[assignment]

    view = DataView(portal=portal, visible_through="20240108")
    closes = view.history("600000.SH", field="close", bar_count=5)
    assert len(closes) == 5
    # No call should start before the snapshot began (19000101 sentinel).
    for _, start, _ in captured:
        assert not start.startswith(
            "19"
        ), f"history called get_bars with legacy {start} start"


# ---------------------------------------------------------------------------
# Performance smoke test (CI-friendly)
# ---------------------------------------------------------------------------


def test_perf_smoke_50_symbols_250_days_history(tmp_path):
    """50 symbols × 250 days, history(bar_count=20) every (symbol, day).

    The legacy portal (pre-task-15) would re-parse each daily file for
    every call. With the cache the total wall time must stay under a
    generous CI threshold. We pick 15 s — far above the expected
    sub-second runtime but well within typical GitHub Actions timeouts.
    """
    from datetime import date, timedelta

    symbols = [f"{600000 + i:06d}.SH" for i in range(50)]
    days = []
    # 250 sequential calendar-valid YYYYMMDD strings starting at 20240102.
    # Earlier the fixture used an integer counter with
    # ``if d % 100 == 32: d += 70`` to skip month boundaries, but that
    # only caught the day-32 rollover after a 31-day month and emitted
    # impossible dates for shorter months (`20240230`, `20240231`,
    # `20240431`, `20240631`) — the bare `len == 8 and isdigit()` check
    # in `validate_yyyymmdd` silently accepted them. Iterating via
    # `datetime` keeps the calendar correct by construction.
    d = date(2024, 1, 2)
    while len(days) < 250:
        days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)

    _build_synthetic_snapshot(tmp_path, symbols=symbols, trading_days=days)
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))

    start = time.monotonic()
    for d in days:
        view = DataView(portal=portal, visible_through=d)
        for sym in symbols:
            view.history(sym, field="close", bar_count=20)
    elapsed = time.monotonic() - start

    # CI-friendly threshold. The actual runtime on a modern machine is
    # well under 1 s for this fixture size.
    assert elapsed < 15.0, f"history perf smoke took {elapsed:.2f}s (>15s)"


# ---------------------------------------------------------------------------
# Bisect correctness
# ---------------------------------------------------------------------------


def test_get_bars_window_returns_correct_slice(tmp_path):
    """Window slicing from the cumulative cache must match per-day reads."""
    symbols = ["600000.SH", "000001.SZ"]
    days = ["20240102", "20240103", "20240104", "20240105", "20240108"]
    _build_synthetic_snapshot(tmp_path, symbols=symbols, trading_days=days)
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))

    full = portal.get_bars("600000.SH", "20240102", "20240108")
    mid = portal.get_bars("600000.SH", "20240103", "20240105")
    late = portal.get_bars("600000.SH", "20240108", "20240108")
    assert [b.date for b in full] == days
    assert [b.date for b in mid] == ["20240103", "20240104", "20240105"]
    assert [b.date for b in late] == ["20240108"]


def test_get_bars_handles_per_symbol_gaps_in_cumulative_cache(tmp_path):
    """A suspended day simply yields no bar in the cumulative cache."""
    symbols = ["600000.SH", "000001.SZ"]
    days = ["20240102", "20240103", "20240104"]
    snap = _build_synthetic_snapshot(tmp_path, symbols=symbols, trading_days=days)
    # Suspend 600000.SH on 20240103 by omitting its row.
    target = snap / "stock_daily" / "20240103.csv"
    lines = target.read_text(encoding="utf-8").splitlines()
    header, rest = lines[0], lines[1:]
    keep = [ln for ln in rest if not ln.startswith("600000.SH,")]
    target.write_text("\n".join([header] + keep) + "\n", encoding="utf-8")

    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    bars = portal.get_bars("600000.SH", "20240102", "20240104")
    assert [b.date for b in bars] == ["20240102", "20240104"]
    # 000001.SZ is unaffected.
    full = portal.get_bars("000001.SZ", "20240102", "20240104")
    assert [b.date for b in full] == days


def test_snapshot_file_missing_propagates_through_cumulative_cache(
    tmp_path, monkeypatch
):
    """If a daily file is missing on disk, the cumulative cache path must
    still raise `SnapshotFileMissingError` rather than silently fold the
    failure into an empty list (missing-file vs per-symbol-gap
    invariant).
    """
    symbols = ["600000.SH"]
    days = ["20240102", "20240103"]
    _build_synthetic_snapshot(tmp_path, symbols=symbols, trading_days=days)
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))

    # First query populates the cache for 20240102.
    portal.get_bars("600000.SH", "20240102", "20240102")
    # Now physically remove the 20240103 file.
    from hqbacktest.data import SnapshotFileMissingError

    (tmp_path / "tushare" / "stock_daily" / "20240103.csv").unlink()
    # Bypass the cached "bars" entry: query a new (start, end) window that
    # would force the portal to consult the daily index for 20240103.
    from hqbacktest.data import hqdata_portal as hp_mod

    portal._daily_index.pop("20240103", None)
    with pytest.raises(SnapshotFileMissingError):
        portal.get_bars("600000.SH", "20240103", "20240103")


def test_get_factor_uses_cumulative_cache(tmp_path):
    """`get_factor` windows return the same factor objects across calls."""
    symbols = ["600000.SH"]
    days = ["20240102", "20240103", "20240104"]
    _build_synthetic_snapshot(tmp_path, symbols=symbols, trading_days=days)
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))

    full = portal.get_factor("600000.SH", "20240102", "20240104")
    late = portal.get_factor("600000.SH", "20240104", "20240104")
    assert [d for d, _ in full] == days
    assert [d for d, _ in late] == ["20240104"]
    # Same Decimal instance (cached tuple element).
    assert full[-1][1] is late[-1][1]


def test_forward_extend_does_not_parse_unqueried_files(tmp_path):
    """Forward extension only reads (cend, end]; never files before the
    already-covered range.

    Regression for a bug where the forward-extend path parsed every
    daily file from the epoch up to `end` — including files the
    strategy never queried. A missing/corrupt file outside the query
    window must not abort the run (per-day / missing-file "distinguishable
    failure" invariant).
    """
    symbols = ["600000.SH"]
    snap = tmp_path / "tushare"
    snap.mkdir(parents=True, exist_ok=True)
    _write_calendar(
        snap,
        [
            ("20240101", "Y"),
            ("20240102", "Y"),
            ("20240103", "Y"),
            ("20240104", "Y"),
            ("20240105", "Y"),
        ],
    )
    _write_stock_list(snap, "20240102", symbols)
    for d in ["20240102", "20240103", "20240104", "20240105"]:
        _write_stock_daily(
            snap,
            d,
            [
                {
                    "symbol": "600000.SH",
                    "date": d,
                    "pre_close": 10,
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10,
                    "volume": 1000,
                    "turnover": 10000,
                    "change": 0,
                    "pct_change": 0,
                }
            ],
        )
    # 20240101.csv is intentionally absent.
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))

    # First query covers 0103-0104 (never touches 0101).
    assert [b.date for b in portal.get_bars("600000.SH", "20240103", "20240104")] == [
        "20240103",
        "20240104",
    ]
    # Forward extension to 0105 must NOT touch the missing 0101 file.
    assert [b.date for b in portal.get_bars("600000.SH", "20240105", "20240105")] == [
        "20240105"
    ]
    # Backward extension to 0102 must not touch 0101 either.
    assert [b.date for b in portal.get_bars("600000.SH", "20240102", "20240102")] == [
        "20240102"
    ]
