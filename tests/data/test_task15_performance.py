"""Performance and cache-reuse smoke tests for task 15.

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


def test_daily_csv_parsed_at_most_once(tmp_path, monkeypatch):
    """`stock_daily/{D}.csv` is read at most once across many `get_bars` calls.

    The portal must parse the file the first time and reuse the resulting
    `dict[symbol, Bar]` for every subsequent query, regardless of the
    requested window.
    """
    symbols = ["600000.SH", "000001.SZ", "688001.SH"]
    days = ["20240102", "20240103", "20240104"]
    _build_synthetic_snapshot(tmp_path, symbols=symbols, trading_days=days)

    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))

    # Wrap pandas.read_csv to count file reads.
    import pandas as pd

    real_read = pd.read_csv
    read_calls: Dict[str, int] = {}

    def counting_read(path, *args, **kwargs):
        p = str(path)
        read_calls[p] = read_calls.get(p, 0) + 1
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(pd, "read_csv", counting_read)
    # Reload module-level pd ref (pandas is imported as `pd`).
    from hqbacktest.data import hqdata_portal as hp_mod

    monkeypatch.setattr(hp_mod.pd, "read_csv", counting_read)

    # Issue many overlapping queries.
    for _ in range(5):
        portal.get_bars("600000.SH", "20240102", "20240104")
    for _ in range(5):
        portal.get_bars("000001.SZ", "20240102", "20240103")
    portal.get_bars("688001.SH", "20240104", "20240104")

    # Three daily files, each read exactly once.
    for d in days:
        path_key = str(tmp_path / "tushare" / "stock_daily" / f"{d}.csv")
        assert (
            read_calls.get(path_key, 0) == 1
        ), f"daily file {d} parsed {read_calls.get(path_key, 0)} times, expected 1"


def test_factor_csv_parsed_at_most_once(tmp_path, monkeypatch):
    """`stock_factor/{D}.csv` is read at most once across queries."""
    symbols = ["600000.SH", "000001.SZ"]
    days = ["20240102", "20240103"]
    _build_synthetic_snapshot(tmp_path, symbols=symbols, trading_days=days)

    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))

    from hqbacktest.data import hqdata_portal as hp_mod

    real_read = hp_mod.pd.read_csv
    read_calls: Dict[str, int] = {}

    def counting_read(path, *args, **kwargs):
        p = str(path)
        read_calls[p] = read_calls.get(p, 0) + 1
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(hp_mod.pd, "read_csv", counting_read)

    portal.get_factor("600000.SH", "20240102", "20240103")
    portal.get_factor("000001.SZ", "20240102", "20240103")
    portal.get_factor("600000.SH", "20240102", "20240102")

    for d in days:
        path_key = str(tmp_path / "tushare" / "stock_factor" / f"{d}.csv")
        assert read_calls.get(path_key, 0) == 1


def test_bar_objects_reused_across_overlapping_queries(tmp_path):
    """`get_bars` overlapping windows must return the same `Bar` instances.

    Memory control: a fresh object per call would multiply allocation by
    the number of overlapping queries. Task 15 says bar objects are
    cached and reused.
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
    symbols = [f"{600000 + i:06d}.SH" for i in range(50)]
    days = []
    # 250 sequential YYYYMMDD strings starting at 20240102, skipping weekends.
    d = 20240102
    while len(days) < 250:
        mmdd = d % 10000
        weekday = mmdd % 7  # rough placeholder; we don't actually skip here
        days.append(f"{d:08d}")
        d += 1
        if d % 100 == 32:
            d += 70  # jump a month to keep within 250 entries
    # Truncate to exactly 250 just in case the loop overshot.
    days = days[:250]

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
    failure into an empty list (task 14 invariant preserved).
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

    Regression for a bug where the forward-extend path used lo="00000000",
    parsing every daily file from the epoch up to `end` — including files
    the strategy never queried. A missing/corrupt file outside the query
    window must not abort the run (task 15 + task 14 "distinguishable
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
