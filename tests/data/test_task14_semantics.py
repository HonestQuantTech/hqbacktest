"""Task 14 tests for DataView and snapshot-missing error handling.

Covers:
    * Sentinel `visible_through="00000000"` on the first trading day must not
      raise; `history` returns [] and `current_price` returns None.
    * `current_price` walks back up to 20 trading days for the most recent
      valid close (suspended-symbol semantics).
    * `history(bar_count=N)` no longer scans the full pre-start window.
    * `SnapshotFileMissingError` is a sibling/child of `MissingDataError` so
      engine / broker can distinguish business gap from infrastructure failure.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from hqbacktest.data import (
    DataView,
    FutureDataAccessError,
    InMemoryDataPortal,
    MissingDataError,
    SnapshotFileMissingError,
)
from hqbacktest.data.data_view import DEFAULT_HISTORY_START
from hqbacktest.domain.bar import Bar
from hqbacktest.engine.engine import BacktestEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bar(date: str, close: str) -> Bar:
    # Wide OHLC envelope so any close in [9, 30] is valid.
    return Bar.from_raw(
        symbol="600000.SH",
        date=date,
        open="10.00",
        high="30.00",
        low="9.00",
        close=close,
        volume=1000,
    )


def _long_calendar_portal() -> InMemoryDataPortal:
    """30 trading days; bars only on every other day from 20240102."""
    dates = [f"2024{(4 + i // 30):04d}{(2 + i % 28):02d}" for i in range(30)]
    # Normalize: take 30 unique YYYYMMDD dates by truncating month overflow
    base = ["20240102"]
    for i in range(1, 30):
        d = int(base[i - 1]) + 1
        # skip weekends (very rough)
        base.append(f"{d:08d}")
    p = InMemoryDataPortal(calendar=base, as_of=base[-1])
    for d in base[::2]:
        p.add_bar(_bar(d, "10.00"))
    return p


# ---------------------------------------------------------------------------
# Sentinel visible_through
# ---------------------------------------------------------------------------


def test_first_trading_day_sentinel_does_not_raise_for_history():
    """Before any trading day, the scheduler uses `00000000` as a sentinel.

    `history` must return an empty list, not raise, so that strategies calling
    `data.history(...)` on the first day do not crash.
    """
    p = _long_calendar_portal()
    view = DataView(portal=p, visible_through="00000000")
    assert view.history("600000.SH", field="close", bar_count=20) == []


def test_first_trading_day_sentinel_does_not_raise_for_current_price():
    p = _long_calendar_portal()
    view = DataView(portal=p, visible_through="00000000")
    assert view.current_price("600000.SH") is None


def test_sentinel_get_bars_returns_empty():
    p = _long_calendar_portal()
    view = DataView(portal=p, visible_through="00000000")
    # Even an explicit get_bars call must return [] (not raise) for the
    # sentinel, so the strategy can detect "no data yet" gracefully.
    assert view.get_bars("600000.SH", "00000000", "00000000") == []


# ---------------------------------------------------------------------------
# current_price: lookback window
# ---------------------------------------------------------------------------


def _gap_portal() -> InMemoryDataPortal:
    """21 trading days; bar on every 5th day only."""
    dates = [f"{d:08d}" for d in range(20240102, 20240102 + 21)]
    p = InMemoryDataPortal(calendar=dates, as_of=dates[-1])
    for i, d in enumerate(dates):
        if i % 5 == 0:
            p.add_bar(_bar(d, str(10 + i / 10)))
    return p


def test_current_price_returns_most_recent_close_within_lookback():
    p = _gap_portal()
    last_bar_date = "20240102"  # the 0th day, the only bar in the first 5
    view = DataView(portal=p, visible_through="20240105")
    # Even though no bar exists on 20240105 itself, the most recent bar
    # within 20 trading days is on 20240102.
    assert view.current_price("600000.SH") == Decimal("10.0000")


def test_current_price_returns_none_when_lookback_exhausted():
    """If no valid close exists within 20 trading days, return None."""
    p = InMemoryDataPortal(calendar=[], as_of="20991231")
    view = DataView(portal=p, visible_through="20240102")
    assert view.current_price("600000.SH") is None


def test_current_price_returns_none_for_symbol_with_no_history():
    p = _gap_portal()
    view = DataView(portal=p, visible_through="20240121")
    # No bar for 000001.SZ at all.
    assert view.current_price("000001.SZ") is None


def test_current_price_does_not_scan_full_history():
    """`current_price` must NOT call get_bars with the full pre-start window.

    Regression for the task-14 finding that `DataView.history` always used
    `19000101→visible_through`, blowing up the data layer cache.
    """
    p = _gap_portal()
    view = DataView(portal=p, visible_through="20240105")

    called: list[tuple[str, str, str]] = []
    original = p.get_bars

    def spy(symbol: str, start: str, end: str):
        called.append((symbol, start, end))
        return original(symbol, start, end)

    p.get_bars = spy  # type: ignore[assignment]
    view.current_price("600000.SH")
    assert called, "expected at least one get_bars call"
    # No call should start at DEFAULT_HISTORY_START (19000101) when an
    # explicit visible_through is provided.
    assert all(start != DEFAULT_HISTORY_START for _, start, _ in called)


# ---------------------------------------------------------------------------
# history semantics
# ---------------------------------------------------------------------------


def test_history_returns_empty_when_window_empty():
    p = _gap_portal()
    view = DataView(portal=p, visible_through="20240105")
    closes = view.history("000001.SZ", field="close", bar_count=5)
    assert closes == []


def test_history_does_not_use_legacy_start_when_universe_start_set():
    p = _gap_portal()
    view = DataView(portal=p, visible_through="20240105", universe_start="20240101")
    called: list[tuple[str, str, str]] = []
    original = p.get_bars

    def spy(symbol: str, start: str, end: str):
        called.append((symbol, start, end))
        return original(symbol, start, end)

    p.get_bars = spy  # type: ignore[assignment]
    view.history("600000.SH", bar_count=5)
    assert all(start == "20240101" for _, start, _ in called)


def test_history_universe_start_before_data_returns_visible_bars():
    """`universe_start` earlier than the actual data must not raise."""
    p = _gap_portal()
    view = DataView(portal=p, visible_through="20240105", universe_start="19800101")
    # universe_start is well before any data; history returns whatever
    # bars are visible in the window, never raising.
    closes = view.history("600000.SH", bar_count=5)
    assert closes, "the window's visible bars must still be returned"
    assert all(Decimal("10") <= c <= Decimal("10") for c in closes)


# ---------------------------------------------------------------------------
# SnapshotFileMissingError classification
# ---------------------------------------------------------------------------


def test_snapshot_file_missing_error_is_subclass_of_missing_data():
    assert issubclass(SnapshotFileMissingError, MissingDataError)


def test_snapshot_file_missing_error_carries_path_info():
    err = SnapshotFileMissingError("stock_daily", "/tmp/foo/stock_daily/20240102.csv")
    msg = str(err)
    assert "stock_daily" in msg
    assert "20240102" in msg


# ---------------------------------------------------------------------------
# Existing behaviour preserved
# ---------------------------------------------------------------------------


def test_history_with_universe_start_returns_only_in_window():
    p = _long_calendar_portal()
    view = DataView(
        portal=p,
        visible_through=p.calendar[-1],
        universe_start=p.calendar[-5],
    )
    closes = view.history("600000.SH", bar_count=10)
    assert 0 < len(closes) <= 5


def test_current_price_returns_latest_close_on_full_coverage():
    p = _long_calendar_portal()
    view = DataView(portal=p, visible_through=p.calendar[-1])
    price = view.current_price("600000.SH")
    assert price is not None
    assert Decimal("10.0000") <= price <= Decimal("10.0000")


# ---------------------------------------------------------------------------
# SnapshotFileMissingError propagation (task 14: infrastructure failure must
# not be silently folded into a per-symbol gap)
# ---------------------------------------------------------------------------


class _SnapshotMissingPortal:
    """A portal whose whole-day snapshot is missing for every query."""

    def get_calendar(self, start: str, end: str) -> list[str]:
        return ["20240102", "20240103", "20240104"]

    def get_bars(self, symbol: str, start: str, end: str) -> list:
        raise SnapshotFileMissingError("stock_daily", f"/tmp/stock_daily/{end}.csv")

    def get_universe(self, date: str, include_bj: bool = False) -> list:
        return []

    def get_factor(self, symbol: str, start: str, end: str) -> list:
        return []


def test_history_propagates_snapshot_missing():
    view = DataView(portal=_SnapshotMissingPortal(), visible_through="20240104")
    with pytest.raises(SnapshotFileMissingError):
        view.history("600000.SH", field="close", bar_count=5)


def test_current_price_propagates_snapshot_missing():
    view = DataView(portal=_SnapshotMissingPortal(), visible_through="20240104")
    with pytest.raises(SnapshotFileMissingError):
        view.current_price("600000.SH")


def test_engine_close_price_propagates_snapshot_missing():
    with pytest.raises(SnapshotFileMissingError):
        BacktestEngine._close_price_or_none(
            _SnapshotMissingPortal(), "600000.SH", "20240104"
        )


def test_engine_lookback_price_propagates_snapshot_missing():
    with pytest.raises(SnapshotFileMissingError):
        BacktestEngine._lookback_price_or_none(
            _SnapshotMissingPortal(), "600000.SH", "20240104"
        )


# ---------------------------------------------------------------------------
# history start bound + early-read error type (task 14)
# ---------------------------------------------------------------------------


def test_history_does_not_query_legacy_19000101_start():
    """`history` must not scan the full pre-start window (task 14)."""
    p = _gap_portal()
    view = DataView(portal=p, visible_through="20240105")

    called: list[tuple[str, str, str]] = []
    original = p.get_bars

    def spy(symbol: str, start: str, end: str):
        called.append((symbol, start, end))
        return original(symbol, start, end)

    p.get_bars = spy  # type: ignore[assignment]
    view.history("600000.SH", field="close", bar_count=5)
    assert called, "expected at least one get_bars call"
    assert all(start != DEFAULT_HISTORY_START for _, start, _ in called)


def test_get_bars_before_universe_start_raises_missing_data_not_future():
    """Reading before the data start is missing data, NOT future-data access."""
    p = _gap_portal()
    view = DataView(portal=p, visible_through="20240105", universe_start="20240103")
    with pytest.raises(MissingDataError) as exc:
        view.get_bars("600000.SH", "20240101", "20240102")
    # Must not be (and must not be reported as) future-data access.
    assert not isinstance(exc.value, FutureDataAccessError)


def test_current_price_none_when_suspended_beyond_lookback():
    """The lookback is bounded by 20 *trading days*, not 20 *bars*.

    A symbol suspended for longer than the lookback must return None (the
    window is exhausted), not reach back to its pre-suspension close. This
    is a regression guard for a task-15 rewrite that briefly trimmed the
    result to the last 20 bars instead of the last 20 trading days.
    """
    days = [f"{20240102 + i:08d}" for i in range(30)]
    p = InMemoryDataPortal(calendar=days, as_of=days[-1])
    for d in days[:3]:
        p.add_bar(_bar(d, "10.00"))
    view = DataView(portal=p, visible_through=days[-1])
    assert view.current_price("600000.SH") is None


def test_current_price_uses_latest_within_20_trading_days():
    """A bar within the 20-trading-day window is still returned."""
    days = [f"{20240102 + i:08d}" for i in range(30)]
    p = InMemoryDataPortal(calendar=days, as_of=days[-1])
    # Bar on day index 15 (well within the last 20 trading days) at 10.5.
    p.add_bar(_bar(days[15], "10.50"))
    view = DataView(portal=p, visible_through=days[-1])
    assert view.current_price("600000.SH") == Decimal("10.5000")
