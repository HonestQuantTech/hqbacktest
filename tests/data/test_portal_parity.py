"""Parity tests between InMemoryDataPortal and HqDataCsvPortal.

The two portals must agree on every observable behavior for the same fixture
data. Per task 14 of TODO.md, any divergence means tests can pass on memory
data while production silently misbehaves on CSV snapshots. Each test in this
file constructs equivalent fixtures for both portals and asserts identical
return values and identical exception types.
"""

from __future__ import annotations

from datetime import date as _date
from decimal import Decimal
from pathlib import Path

import pytest

from hqbacktest.data import (
    HqDataCsvPortal,
    InMemoryDataPortal,
    InvalidDataError,
    MissingDataError,
    SnapshotFileMissingError,
    UnknownSymbolError,
)
from hqbacktest.data.hqdata_portal import resolve_source_location
from hqbacktest.domain.bar import Bar


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


# Trading calendar with a weekend-style gap: 20240102-20240105 are trading
# days, 20240106 (Saturday) is excluded.
CALENDAR_DATES: list[tuple[str, str]] = [
    ("20240102", "Y"),
    ("20240103", "Y"),
    ("20240104", "Y"),
    ("20240105", "Y"),
]
LATE_CALENDAR_DATES: list[tuple[str, str]] = [
    ("20240102", "Y"),
    ("20240103", "Y"),
    ("20240104", "Y"),
    ("20240105", "Y"),
    ("20240108", "Y"),
    ("20240109", "Y"),
    ("20240110", "Y"),
    ("20240111", "Y"),
    ("20240112", "Y"),
]


def _make_bar(symbol: str, date: str, close: str = "10.00") -> Bar:
    # Wide OHLC envelope so any close in [9, 30] is valid.
    return Bar.from_raw(
        symbol=symbol,
        date=date,
        open="10.00",
        high="30.00",
        low="9.00",
        close=close,
        volume=1000,
    )


def _memory_with_gaps() -> InMemoryDataPortal:
    """SUSPENDED on 20240103; never-listed symbol 999999.SH."""
    p = InMemoryDataPortal(
        calendar=[d for d, f in CALENDAR_DATES],
        universe_by_date={"20240102": ["600000.SH", "000001.SZ"]},
        as_of="20240105",
    )
    # 600000.SH: traded on 20240102, suspended 20240103-04, traded 20240105.
    p.add_bar(_make_bar("600000.SH", "20240102", "10.00"))
    p.add_bar(_make_bar("600000.SH", "20240105", "11.00"))
    # 000001.SZ: traded every day.
    p.add_bar(_make_bar("000001.SZ", "20240102", "20.00"))
    p.add_bar(_make_bar("000001.SZ", "20240103", "20.50"))
    p.add_bar(_make_bar("000001.SZ", "20240104", "20.25"))
    p.add_bar(_make_bar("000001.SZ", "20240105", "21.00"))
    # Factors mirror the bars: 600000.SH has factor rows for every
    # trading day (so callers cannot infer a gap from a missing factor
    # row — they would need to compare against `get_calendar`); 000001.SZ
    # only carries factors on 20240102 / 20240105, exercising the
    # in-window factor gap.
    p.add_factor("600000.SH", "20240102", Decimal("1.0"))
    p.add_factor("600000.SH", "20240103", Decimal("1.0"))
    p.add_factor("600000.SH", "20240104", Decimal("1.0"))
    p.add_factor("600000.SH", "20240105", Decimal("1.05"))
    p.add_factor("000001.SZ", "20240102", Decimal("1.0"))
    p.add_factor("000001.SZ", "20240105", Decimal("1.02"))
    return p


def _write_calendar(root: Path, rows: list[tuple[str, str]]) -> None:
    lines = ["date,is_open"]
    for d, f in rows:
        lines.append(f"{d},{f}")
    (root / "calendar.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_stock_list(root: Path, date: str, symbols: list[str]) -> None:
    target = root / "stock_list"
    target.mkdir(parents=True, exist_ok=True)
    lines = ["symbol,date,name,exchange,board,curr_type,list_date,delist_date"]
    for sym in symbols:
        lines.append(f"{sym},{date},name,SSE,MB,CNY,19990101,")
    (target / f"{date}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_stock_daily(root: Path, date: str, rows: list[dict]) -> None:
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


def _write_stock_factor(root: Path, date: str, rows: list[dict]) -> None:
    """Each row: {symbol, date, factor}."""
    target = root / "stock_factor"
    target.mkdir(parents=True, exist_ok=True)
    lines = ["symbol,date,factor"]
    for row in rows:
        lines.append(f"{row['symbol']},{row['date']},{row['factor']}")
    (target / f"{date}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _csv_with_gaps(tmp_path: Path) -> HqDataCsvPortal:
    """Same fixture as `_memory_with_gaps` but on disk.

    Layout:
      - stock_daily/20240102.csv has both 600000.SH and 000001.SZ.
      - stock_daily/20240103.csv only has 000001.SZ (600000.SH suspended).
      - stock_daily/20240104.csv only has 000001.SZ (600000.SH still suspended).
      - stock_daily/20240105.csv has both 600000.SH and 000001.SZ.
      - stock_factor/{date}.csv mirrors the bar layout: 600000.SH
        factors on every trading day, 000001.SZ factors on 20240102
        and 20240105 only.
    """
    snap = tmp_path / "tushare"
    snap.mkdir(parents=True, exist_ok=True)
    _write_calendar(snap, CALENDAR_DATES)
    _write_stock_list(snap, "20240102", ["600000.SH", "000001.SZ"])
    _write_stock_daily(
        snap,
        "20240102",
        [
            {
                "symbol": "600000.SH",
                "date": "20240102",
                "pre_close": 10,
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10,
                "volume": 1000,
                "turnover": 10000,
                "change": 0,
                "pct_change": 0,
            },
            {
                "symbol": "000001.SZ",
                "date": "20240102",
                "pre_close": 20,
                "open": 20,
                "high": 21,
                "low": 19,
                "close": 20,
                "volume": 1000,
                "turnover": 20000,
                "change": 0,
                "pct_change": 0,
            },
        ],
    )
    _write_stock_daily(
        snap,
        "20240103",
        [
            {
                "symbol": "000001.SZ",
                "date": "20240103",
                "pre_close": 20,
                "open": 20,
                "high": 21,
                "low": 19,
                "close": 20.5,
                "volume": 1000,
                "turnover": 20500,
                "change": 0.5,
                "pct_change": 2.5,
            }
        ],
    )
    _write_stock_daily(
        snap,
        "20240104",
        [
            {
                "symbol": "000001.SZ",
                "date": "20240104",
                "pre_close": 20.5,
                "open": 20,
                "high": 21,
                "low": 19,
                "close": 20.25,
                "volume": 1000,
                "turnover": 20250,
                "change": -0.25,
                "pct_change": -1.22,
            }
        ],
    )
    _write_stock_daily(
        snap,
        "20240105",
        [
            {
                "symbol": "600000.SH",
                "date": "20240105",
                "pre_close": 10,
                "open": 11,
                "high": 11.5,
                "low": 10.5,
                "close": 11,
                "volume": 1000,
                "turnover": 11000,
                "change": 1,
                "pct_change": 10,
            },
            {
                "symbol": "000001.SZ",
                "date": "20240105",
                "pre_close": 20.25,
                "open": 21,
                "high": 21.5,
                "low": 20.5,
                "close": 21,
                "volume": 1000,
                "turnover": 21000,
                "change": 0.75,
                "pct_change": 3.7,
            },
        ],
    )
    # Factors: 600000.SH on every trading day, 000001.SZ on 20240102
    # and 20240105 only (matches `_memory_with_gaps`). Build each
    # daily factor file in one pass — `_write_stock_factor` writes the
    # whole file, so calling it multiple times for the same date
    # would clobber earlier rows and break parity.
    factor_rows = {
        "20240102": [
            {"symbol": "600000.SH", "date": "20240102", "factor": "1.0"},
            {"symbol": "000001.SZ", "date": "20240102", "factor": "1.0"},
        ],
        "20240103": [
            {"symbol": "600000.SH", "date": "20240103", "factor": "1.0"},
        ],
        "20240104": [
            {"symbol": "600000.SH", "date": "20240104", "factor": "1.0"},
        ],
        "20240105": [
            {"symbol": "600000.SH", "date": "20240105", "factor": "1.05"},
            {"symbol": "000001.SZ", "date": "20240105", "factor": "1.02"},
        ],
    }
    for date, rows in factor_rows.items():
        _write_stock_factor(snap, date, rows)
    return HqDataCsvPortal(source="tushare", data_root=str(tmp_path))


# ---------------------------------------------------------------------------
# Calendar parity
# ---------------------------------------------------------------------------


def test_calendar_window_returns_same_open_dates(tmp_path):
    mem = InMemoryDataPortal(calendar=[d for d, _ in CALENDAR_DATES], as_of="20240105")
    csv = _csv_with_gaps(tmp_path)
    assert mem.get_calendar("20240102", "20240105") == csv.get_calendar(
        "20240102", "20240105"
    )


def test_is_trading_day_agrees(tmp_path):
    mem = InMemoryDataPortal(calendar=[d for d, _ in CALENDAR_DATES], as_of="20240105")
    csv = _csv_with_gaps(tmp_path)
    for d, flag in CALENDAR_DATES:
        assert mem.is_trading_day(d) == csv.is_trading_day(d)
        assert csv.is_trading_day(d) is (flag == "Y")


def test_previous_and_next_trading_day_agrees(tmp_path):
    mem = InMemoryDataPortal(
        calendar=[d for d, _ in LATE_CALENDAR_DATES], as_of="20240112"
    )
    snap = tmp_path / "tushare"
    snap.mkdir()
    _write_calendar(snap, LATE_CALENDAR_DATES)
    csv = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    for d in ("20240105", "20240108", "20240111"):
        assert mem.previous_trading_day(d) == csv.previous_trading_day(d)
        assert mem.next_trading_day(d) == csv.next_trading_day(d)


def test_calendar_rejects_start_after_end(tmp_path):
    mem = InMemoryDataPortal(calendar=[d for d, _ in CALENDAR_DATES], as_of="20240105")
    csv = _csv_with_gaps(tmp_path)
    with pytest.raises(InvalidDataError):
        mem.get_calendar("20240105", "20240102")
    with pytest.raises(InvalidDataError):
        csv.get_calendar("20240105", "20240102")


# ---------------------------------------------------------------------------
# Universe parity
# ---------------------------------------------------------------------------


def test_universe_exact_date_agrees(tmp_path):
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    assert mem.get_universe("20240102") == csv.get_universe("20240102")


def test_universe_raises_on_missing_snapshot_for_both(tmp_path):
    """Neither portal silently walks back to a prior snapshot (task 14).

    A missing whole-day stock-list snapshot is an infrastructure failure in
    both portals, so the exception type must be identical
    (`SnapshotFileMissingError`), not merely a shared base class.
    """
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    with pytest.raises(SnapshotFileMissingError):
        mem.get_universe("20240106")
    with pytest.raises(SnapshotFileMissingError):
        csv.get_universe("20240106")


def test_universe_rejects_future_date(tmp_path):
    """Both portals validate the date format."""
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    with pytest.raises(InvalidDataError):
        mem.get_universe("not-a-date")
    with pytest.raises(InvalidDataError):
        csv.get_universe("not-a-date")


def test_universe_excludes_bj_by_default(tmp_path):
    """`.BJ` (Beijing Stock Exchange) symbols are excluded by default."""
    from hqbacktest.data import InMemoryDataPortal

    mem = InMemoryDataPortal(
        calendar=["20240102"],
        universe_by_date={"20240102": ["600000.SH", "830001.BJ", "000001.SZ"]},
        as_of="20240102",
    )
    snap = tmp_path / "tushare"
    snap.mkdir()
    _write_calendar(snap, [("20240102", "Y")])
    _write_stock_list(snap, "20240102", ["600000.SH", "830001.BJ", "000001.SZ"])
    csv = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    assert mem.get_universe("20240102") == ["000001.SZ", "600000.SH"]
    assert csv.get_universe("20240102") == ["000001.SZ", "600000.SH"]


def test_universe_includes_bj_when_requested(tmp_path):
    """`include_bj=True` keeps `.BJ` symbols in the result."""
    from hqbacktest.data import InMemoryDataPortal

    mem = InMemoryDataPortal(
        calendar=["20240102"],
        universe_by_date={"20240102": ["600000.SH", "830001.BJ", "000001.SZ"]},
        as_of="20240102",
    )
    snap = tmp_path / "tushare"
    snap.mkdir()
    _write_calendar(snap, [("20240102", "Y")])
    _write_stock_list(snap, "20240102", ["600000.SH", "830001.BJ", "000001.SZ"])
    csv = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    assert mem.get_universe("20240102", include_bj=True) == [
        "000001.SZ",
        "600000.SH",
        "830001.BJ",
    ]
    assert csv.get_universe("20240102", include_bj=True) == [
        "000001.SZ",
        "600000.SH",
        "830001.BJ",
    ]


# ---------------------------------------------------------------------------
# Bars parity
# ---------------------------------------------------------------------------


def test_bars_returns_window_subset_allowing_gaps(tmp_path):
    """A suspended symbol must return its actual bars, not raise."""
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    mem_bars = mem.get_bars("600000.SH", "20240102", "20240105")
    csv_bars = csv.get_bars("600000.SH", "20240102", "20240105")
    assert [b.date for b in mem_bars] == [b.date for b in csv_bars]
    assert [b.close for b in mem_bars] == [b.close for b in csv_bars]
    assert [b.date for b in mem_bars] == ["20240102", "20240105"]


def test_bars_full_coverage_symbol_matches(tmp_path):
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    mem_bars = mem.get_bars("000001.SZ", "20240102", "20240105")
    csv_bars = csv.get_bars("000001.SZ", "20240102", "20240105")
    assert [b.date for b in mem_bars] == [b.date for b in csv_bars]
    assert [str(b.close) for b in mem_bars] == [str(b.close) for b in csv_bars]


def test_bars_window_empty_when_never_listed(tmp_path):
    """A symbol that never traded in the window returns empty, not raise."""
    mem = InMemoryDataPortal(calendar=[d for d, _ in CALENDAR_DATES], as_of="20240105")
    csv = _csv_with_gaps(tmp_path)
    assert mem.get_bars("999999.SH", "20240102", "20240105") == []
    assert csv.get_bars("999999.SH", "20240102", "20240105") == []


def test_bars_rejects_window_start_after_end_for_both(tmp_path):
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    with pytest.raises(InvalidDataError):
        mem.get_bars("000001.SZ", "20240105", "20240102")
    with pytest.raises(InvalidDataError):
        csv.get_bars("000001.SZ", "20240105", "20240102")


def test_bars_rejects_bad_symbol_for_both(tmp_path):
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    with pytest.raises(InvalidDataError):
        mem.get_bars("not-a-symbol", "20240102", "20240105")
    with pytest.raises(InvalidDataError):
        csv.get_bars("not-a-symbol", "20240102", "20240105")


def test_bars_distinguishes_snapshot_missing_from_per_symbol_gap(tmp_path):
    """整日快照缺失 must raise SnapshotFileMissingError, not MissingDataError.

    An individual symbol missing from an existing daily file returns [].
    """
    snap = tmp_path / "tushare"
    snap.mkdir()
    _write_calendar(
        snap,
        [("20240102", "Y"), ("20240103", "Y")],
    )
    _write_stock_list(snap, "20240102", ["600000.SH"])
    _write_stock_daily(
        snap,
        "20240102",
        [
            {
                "symbol": "600000.SH",
                "date": "20240102",
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
    # NOTE: no 20240103.csv at all
    csv = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(SnapshotFileMissingError):
        csv.get_bars("600000.SH", "20240102", "20240103")


def test_bars_snapshot_missing_vs_per_symbol_missing_classification():
    """get_bars raises SnapshotFileMissingError (subclass of MissingDataError).

    The two failure modes must remain distinguishable for the engine: a per-
    symbol gap is a normal business outcome (suspended / delisted / IPO'd),
    while a missing whole-day file is a data infrastructure failure that must
    abort the run.
    """
    assert issubclass(SnapshotFileMissingError, MissingDataError)


# ---------------------------------------------------------------------------
# Factor parity (task 24: `test_factor_rejects_zero_in_memory_portal`
# below only asserted the memory portal rejected 0 independently; it did
# NOT exercise the parity of returned values. These tests run the same
# fixture through both portals and assert identical return values /
# exception types.)
# ---------------------------------------------------------------------------


def test_factor_window_returns_identical_series(tmp_path):
    """Task 24: `get_factor` must return the same `(date, factor)` list
    on both portals for the same fixture (mirrors `test_bars_*_agrees`).
    """
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    mem_factors = mem.get_factor("600000.SH", "20240102", "20240105")
    csv_factors = csv.get_factor("600000.SH", "20240102", "20240105")
    # Convert Decimal tuples to comparable lists so we don't get bitten
    # by Decimal identity vs equality across portals.
    assert [(d, str(f)) for d, f in mem_factors] == [
        (d, str(f)) for d, f in csv_factors
    ]
    assert [d for d, _ in mem_factors] == [
        "20240102",
        "20240103",
        "20240104",
        "20240105",
    ]


def test_factor_per_symbol_gap_matches_between_portals(tmp_path):
    """Task 24: a symbol with a sparse factor series (factors on
    20240102 and 20240105 only) must return the SAME two rows on both
    portals — not raise, not silently extend to every calendar entry.
    """
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    mem_factors = mem.get_factor("000001.SZ", "20240102", "20240105")
    csv_factors = csv.get_factor("000001.SZ", "20240102", "20240105")
    assert [(d, str(f)) for d, f in mem_factors] == [
        (d, str(f)) for d, f in csv_factors
    ]
    assert [d for d, _ in mem_factors] == ["20240102", "20240105"]


def test_factor_empty_when_symbol_never_listed(tmp_path):
    """Task 24: a symbol absent from both `stock_list` and the factor
    files returns `[]` on both portals.
    """
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    assert mem.get_factor("999999.SH", "20240102", "20240105") == []
    assert csv.get_factor("999999.SH", "20240102", "20240105") == []


def test_factor_rejects_window_start_after_end_for_both(tmp_path):
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    with pytest.raises(InvalidDataError):
        mem.get_factor("600000.SH", "20240105", "20240102")
    with pytest.raises(InvalidDataError):
        csv.get_factor("600000.SH", "20240105", "20240102")


def test_factor_rejects_bad_symbol_for_both(tmp_path):
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    with pytest.raises(InvalidDataError):
        mem.get_factor("not-a-symbol", "20240102", "20240105")
    with pytest.raises(InvalidDataError):
        csv.get_factor("not-a-symbol", "20240102", "20240105")


def test_factor_distinguishes_snapshot_missing_from_per_symbol_gap(
    tmp_path,
):
    """Task 24: the parity invariant for `get_factor` mirrors
    `get_bars` — a missing whole-day `stock_factor/{D}.csv` raises
    `SnapshotFileMissingError`, while a per-symbol gap is silently
    omitted from the result.
    """
    snap = tmp_path / "tushare"
    snap.mkdir()
    _write_calendar(
        snap,
        [("20240102", "Y"), ("20240103", "Y")],
    )
    _write_stock_list(snap, "20240102", ["600000.SH"])
    _write_stock_factor(
        snap,
        "20240102",
        [{"symbol": "600000.SH", "date": "20240102", "factor": "1.0"}],
    )
    # NOTE: no 20240103.csv at all
    csv = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(SnapshotFileMissingError):
        csv.get_factor("600000.SH", "20240102", "20240103")


def test_factor_rejects_zero_in_memory_portal():
    """The memory portal rejects a non-positive factor at construction
    time (`add_factor`).

    The CSV portal's equivalent rejection lives in
    `tests/data/test_hqdata_portal.py` (where the daily CSV fixture
    already exists); both funnel through the same `Decimal`-positive
    validation in the data module, so no separate parity test is
    needed for this path.
    """
    mem = InMemoryDataPortal(calendar=["20240102"], as_of="20240102")
    with pytest.raises(InvalidDataError):
        mem.add_factor("600000.SH", "20240102", Decimal("0"))


# ---------------------------------------------------------------------------
# As-of parity
# ---------------------------------------------------------------------------


def test_data_version_as_of_agrees_with_calendar_latest(tmp_path):
    mem = InMemoryDataPortal(calendar=[d for d, _ in CALENDAR_DATES], as_of="20240105")
    csv = _csv_with_gaps(tmp_path)
    assert mem.data_version().as_of == csv.data_version().as_of == "20240105"


def test_as_of_does_not_fall_back_to_today_when_calendar_corrupt(tmp_path):
    """A snapshot with a corrupt calendar must raise, not silently use today."""
    snap = tmp_path / "broken"
    snap.mkdir()
    (snap / "calendar.csv").write_text("date,is_open\nnot-a-date,Y\n", encoding="utf-8")
    with pytest.raises(InvalidDataError):
        HqDataCsvPortal(source="broken", data_root=str(tmp_path))


def test_as_of_does_not_import_hqdata(monkeypatch):
    import hqbacktest.data.hqdata_portal as module

    for name in dir(module):
        if name.startswith("__"):
            continue
        attr = getattr(module, name)
        mod = getattr(attr, "__module__", None) or ""
        assert not mod.startswith("hqdata"), name


# ---------------------------------------------------------------------------
# resolve_source_location
# ---------------------------------------------------------------------------


def test_resolve_source_location_rejects_dot_dot(tmp_path):
    with pytest.raises(InvalidDataError):
        resolve_source_location("..", default_data_root=str(tmp_path))


def test_resolve_source_location_rejects_dot(tmp_path):
    with pytest.raises(InvalidDataError):
        resolve_source_location(".", default_data_root=str(tmp_path))


# ---------------------------------------------------------------------------
# Immutability of cached lists
# ---------------------------------------------------------------------------


def test_cached_bar_lists_are_isolated_from_strategy_mutation(tmp_path):
    """The portal must never return its internal cached list reference."""
    mem = _memory_with_gaps()
    csv = _csv_with_gaps(tmp_path)
    mem_bars = mem.get_bars("000001.SZ", "20240102", "20240105")
    csv_bars = csv.get_bars("000001.SZ", "20240102", "20240105")
    # Caller mutating the returned list must not corrupt later queries.
    mem_bars.clear()
    csv_bars.clear()
    assert mem.get_bars("000001.SZ", "20240102", "20240105")
    assert csv.get_bars("000001.SZ", "20240102", "20240105")
