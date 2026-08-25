"""Tests for InMemoryDataPortal."""

from decimal import Decimal

import pytest

from hqbacktest.data import (
    InMemoryDataPortal,
    InvalidDataError,
    MissingDataError,
    SnapshotFileMissingError,
)
from hqbacktest.domain.bar import Bar


def _bar(symbol: str, date: str, close: str = "10.00") -> Bar:
    return Bar.from_raw(
        symbol=symbol,
        date=date,
        open="10.00",
        high="11.00",
        low="9.00",
        close=close,
        volume=1000,
    )


def test_empty_portal_calendar_and_universe():
    p = InMemoryDataPortal()
    assert p.get_calendar("20240101", "20240131") == []
    assert p.data_version().source == "memory"


def test_calendar_window_returns_sorted_subset():
    p = InMemoryDataPortal(calendar=["20240102", "20240103", "20240104"])
    assert p.get_calendar("20240102", "20240104") == [
        "20240102",
        "20240103",
        "20240104",
    ]
    assert p.get_calendar("20240103", "20240105") == ["20240103", "20240104"]


def test_calendar_rejects_duplicate_generator_input():
    with pytest.raises(InvalidDataError, match="duplicate"):
        InMemoryDataPortal(calendar=(d for d in ["20240102", "20240102"]))


def test_is_trading_day_uses_calendar():
    p = InMemoryDataPortal(calendar=["20240102", "20240103"])
    assert p.is_trading_day("20240102")
    assert not p.is_trading_day("20240104")


def test_previous_and_next_trading_day():
    p = InMemoryDataPortal(calendar=["20240102", "20240103", "20240104"])
    assert p.previous_trading_day("20240103") == "20240102"
    assert p.next_trading_day("20240103") == "20240104"


def test_previous_trading_day_raises_at_first_day():
    p = InMemoryDataPortal(calendar=["20240102"])
    with pytest.raises(MissingDataError):
        p.previous_trading_day("20240102")


def test_next_trading_day_raises_at_last_day():
    p = InMemoryDataPortal(calendar=["20240102"])
    with pytest.raises(MissingDataError):
        p.next_trading_day("20240102")


def test_get_bars_filters_window():
    p = InMemoryDataPortal()
    p.add_bar(_bar("600000.SH", "20240102"))
    p.add_bar(_bar("600000.SH", "20240103", "10.50"))
    p.add_bar(_bar("600000.SH", "20240104", "10.80"))
    bars = p.get_bars("600000.SH", "20240103", "20240104")
    assert [b.date for b in bars] == ["20240103", "20240104"]
    assert [b.close for b in bars] == [Decimal("10.5000"), Decimal("10.8000")]


def test_get_bars_rejects_window_start_after_end():
    p = InMemoryDataPortal()
    with pytest.raises(InvalidDataError):
        p.get_bars("600000.SH", "20240105", "20240102")


def test_get_bars_returns_empty_when_window_empty():
    """Per-symbol gap (no bars for symbol in window) returns [] (per-symbol gap semantics)."""
    p = InMemoryDataPortal()
    p.add_bar(_bar("600000.SH", "20240102"))
    assert p.get_bars("600000.SH", "20240201", "20240205") == []


def test_get_bars_rejects_invalid_symbol():
    p = InMemoryDataPortal()
    with pytest.raises(InvalidDataError):
        p.get_bars("not-a-symbol", "20240102", "20240105")


def test_universe_exact_date_only_no_walk_back():
    """Per-date snapshot semantics; no implicit walk-back.

    The in-memory portal must match the production CSV portal which only
    looks up the snapshot for the exact requested date. This avoids the
    silent forward-fallback that violated contract §4 (stock list must be
    queried per backtest day, not by walking back to the most recent
    snapshot).
    """
    p = InMemoryDataPortal(
        universe_by_date={
            "20240102": ["600000.SH", "000001.SZ"],
            "20240105": ["600000.SH", "000002.SZ", "688001.SH"],
        }
    )
    assert p.get_universe("20240102") == ["000001.SZ", "600000.SH"]
    assert p.get_universe("20240105") == ["000002.SZ", "600000.SH", "688001.SH"]
    # Walk-back is no longer supported.
    with pytest.raises(SnapshotFileMissingError):
        p.get_universe("20240103")
    with pytest.raises(SnapshotFileMissingError):
        p.get_universe("20240110")


def test_universe_raises_when_no_snapshot_exists():
    p = InMemoryDataPortal(universe_by_date={"20240102": ["600000.SH"]})
    with pytest.raises(SnapshotFileMissingError):
        p.get_universe("20200101")


def test_constructor_rejects_duplicate_universe_symbols():
    with pytest.raises(InvalidDataError, match="duplicate"):
        InMemoryDataPortal(universe_by_date={"20240102": ["600000.SH", "600000.SH"]})


def test_add_bar_rejects_duplicate_date():
    p = InMemoryDataPortal()
    p.add_bar(_bar("600000.SH", "20240102"))
    with pytest.raises(InvalidDataError):
        p.add_bar(_bar("600000.SH", "20240102"))
    assert [bar.date for bar in p.bars_by_symbol["600000.SH"]] == ["20240102"]


def test_add_bar_rejects_unsorted_insert():
    p = InMemoryDataPortal()
    p.add_bar(_bar("600000.SH", "20240105"))
    with pytest.raises(InvalidDataError):
        p.add_bar(_bar("600000.SH", "20240102"))
    assert [bar.date for bar in p.bars_by_symbol["600000.SH"]] == ["20240105"]


def test_factor_window():
    p = InMemoryDataPortal()
    p.add_factor("600000.SH", "20240102", Decimal("1.0"))
    p.add_factor("600000.SH", "20240103", Decimal("1.05"))
    p.add_factor("600000.SH", "20240104", Decimal("1.07"))
    rows = p.get_factor("600000.SH", "20240103", "20240104")
    assert rows == [("20240103", Decimal("1.05")), ("20240104", Decimal("1.07"))]


def test_factor_rejects_non_positive():
    p = InMemoryDataPortal()
    with pytest.raises(InvalidDataError):
        p.add_factor("600000.SH", "20240102", Decimal(0))
    with pytest.raises(InvalidDataError):
        p.add_factor("600000.SH", "20240102", Decimal(-1))


# --------------------------------------------------------------------- #
# 停牌 / 缺行 / 交易日覆盖范围
# --------------------------------------------------------------------- #


def test_add_bar_rejects_non_trading_day_when_calendar_known():
    """Bars must land on dates inside the trading calendar."""
    p = InMemoryDataPortal(calendar=["20240102", "20240103", "20240104"])
    bar = _bar("600000.SH", "20240105")  # not in the calendar
    with pytest.raises(InvalidDataError) as exc:
        p.add_bar(bar)
    assert "not in the configured trading calendar" in str(exc.value)


def test_add_bar_accepts_trading_day():
    p = InMemoryDataPortal(calendar=["20240102", "20240103", "20240104"])
    p.add_bar(_bar("600000.SH", "20240102"))
    assert p.bars_by_symbol["600000.SH"][0].date == "20240102"


def test_add_bar_skips_calendar_check_when_calendar_empty():
    """When the calendar is not yet populated, bars are accepted."""
    p = InMemoryDataPortal()
    p.add_bar(_bar("600000.SH", "20240102"))
    assert p.bars_by_symbol["600000.SH"][0].date == "20240102"


def test_constructor_rejects_initial_bar_outside_known_calendar():
    with pytest.raises(InvalidDataError, match="configured trading calendar"):
        InMemoryDataPortal(
            calendar=["20240102"],
            bars_by_symbol={"600000.SH": [_bar("600000.SH", "20240103")]},
        )


def test_missing_bar_on_trading_day_returns_empty_list():
    """Empty-window (no trading days) returns [], not MissingDataError.

    "停牌/缺行" is a per-symbol gap, a normal business outcome. The
    portal surfaces it as an empty list so the caller (engine, DataView)
    can decide the policy.
    """
    p = InMemoryDataPortal(calendar=["20240102", "20240103", "20240104"])
    # No bars at all -> empty list, not an error.
    assert p.get_bars("600000.SH", "20240102", "20240103") == []


def test_window_with_only_suspended_days_returns_empty():
    p = InMemoryDataPortal(calendar=["20240102", "20240103"])
    # No bars at all on a valid trading-day window.
    assert p.get_bars("600000.SH", "20240102", "20240103") == []


def test_partial_window_returns_available_bars():
    """Partial coverage: a missing trading day surfaces as a gap in the result,
    not as a hard error, so callers (engine / DataView) can decide policy."""
    p = InMemoryDataPortal(calendar=["20240102", "20240103", "20240104", "20240105"])
    p.add_bar(_bar("600000.SH", "20240102"))
    p.add_bar(_bar("600000.SH", "20240104"))
    bars = p.get_bars("600000.SH", "20240102", "20240104")
    assert [b.date for b in bars] == ["20240102", "20240104"]
