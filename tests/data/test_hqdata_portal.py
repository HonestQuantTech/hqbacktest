"""Tests for HqDataCsvPortal using temporary CSV fixtures.

Each test builds a self-contained snapshot directory under `tmp_path`,
then exercises the portal end-to-end without touching the host filesystem,
the network, or `hqdata`.
"""

import os
from decimal import Decimal
from pathlib import Path

import pytest

from hqbacktest.data import (
    DEFAULT_DATA_ROOT,
    HqDataCsvPortal,
    InvalidDataError,
    MissingDataError,
    UnknownSymbolError,
)
from hqbacktest.data.cache import CacheKey
from hqbacktest.data.hqdata_portal import resolve_source_location


# --------------------------------------------------------------------- #
# CSV fixture helpers
# --------------------------------------------------------------------- #


def _write_calendar(root: Path, dates_open: list[tuple[str, str]]) -> None:
    """`dates_open` is a list of (YYYYMMDD, 'Y'|'N')."""
    lines = ["date,is_open"]
    for date, flag in dates_open:
        lines.append(f"{date},{flag}")
    (root / "calendar.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_stock_list(root: Path, date: str, rows: list[dict]) -> None:
    """Each row: {symbol, date, name, exchange, board, curr_type, list_date, delist_date}."""
    target = root / "stock_list"
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{date}.csv"
    fields = [
        "symbol",
        "date",
        "name",
        "exchange",
        "board",
        "curr_type",
        "list_date",
        "delist_date",
    ]
    lines = [",".join(fields)]
    for row in rows:
        lines.append(",".join(str(row.get(f, "")) for f in fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_stock_daily(root: Path, date: str, rows: list[dict]) -> None:
    """Each row: {symbol, date, pre_close, open, high, low, close, volume, turnover, change, pct_change}."""
    target = root / "stock_daily"
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{date}.csv"
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
    for row in rows:
        lines.append(",".join(str(row[f]) for f in fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_stock_factor(root: Path, date: str, rows: list[dict]) -> None:
    """Each row: {symbol, date, factor}."""
    target = root / "stock_factor"
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{date}.csv"
    lines = ["symbol,date,factor"]
    for row in rows:
        lines.append(f"{row['symbol']},{row['date']},{row['factor']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_snapshot(
    root: Path,
    source: str,
    calendar_dates: list[tuple[str, str]],
    daily: dict[str, list[dict]],
    lists: dict[str, list[dict]],
    factors: dict[str, list[dict]],
) -> Path:
    """Build a full snapshot under `<root>/<source>` and return that path."""
    snap = root / source
    snap.mkdir(parents=True, exist_ok=True)
    _write_calendar(snap, calendar_dates)
    for date, rows in lists.items():
        _write_stock_list(snap, date, rows)
    for date, rows in daily.items():
        _write_stock_daily(snap, date, rows)
    for date, rows in factors.items():
        _write_stock_factor(snap, date, rows)
    return snap


# --------------------------------------------------------------------- #
# Source location resolution
# --------------------------------------------------------------------- #


def test_resolve_source_location_with_name_uses_default_root(tmp_path):
    data_root, name = resolve_source_location(
        "tushare", default_data_root=str(tmp_path)
    )
    assert data_root == str(tmp_path)
    assert name == "tushare"


def test_resolve_source_location_rejects_source_path(tmp_path):
    with pytest.raises(InvalidDataError, match="directory name"):
        resolve_source_location(
            str(tmp_path / "ricequant"), default_data_root="ignored"
        )


def test_resolve_source_location_rejects_empty():
    with pytest.raises(InvalidDataError):
        resolve_source_location("", default_data_root="x")


def test_resolve_source_location_rejects_path_without_name():
    with pytest.raises(InvalidDataError):
        resolve_source_location("/", default_data_root="x")


# --------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------- #


def test_construction_resolves_snapshot_root(tmp_path):
    snap = tmp_path / "tushare"
    snap.mkdir()
    _write_calendar(
        snap,
        [("20240102", "Y"), ("20240103", "Y"), ("20240104", "Y")],
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    assert portal.source_name() == "tushare"
    assert portal.snapshot_root() == snap
    assert portal.data_root() == Path(str(tmp_path))
    assert portal.data_version().source == "tushare"


def test_construction_rejects_absolute_path_source(tmp_path):
    with pytest.raises(InvalidDataError, match="directory name"):
        HqDataCsvPortal(source=str(tmp_path / "ricequant"))


def test_construction_rejects_empty_source():
    with pytest.raises(InvalidDataError):
        HqDataCsvPortal(source="")


def test_construction_records_latest_open_trading_day_as_as_of(tmp_path):
    snap = _build_snapshot(
        tmp_path,
        "tushare",
        [("20240102", "N"), ("20240103", "Y"), ("20240104", "Y")],
        daily={},
        lists={},
        factors={},
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    assert portal.data_version().as_of == "20240104"


def test_construction_does_not_import_hqdata():
    """Static guard: `hqdata_portal` must not transitively depend on hqdata."""
    import hqbacktest.data.hqdata_portal as module

    for name in dir(module):
        if name.startswith("__"):
            continue
        attr = getattr(module, name)
        mod = getattr(attr, "__module__", None)
        if mod is None:
            continue
        assert not mod.startswith(
            "hqdata"
        ), f"{name} is bound to {mod}; hqdata is forbidden in the data layer"
        assert not mod.startswith(
            "hqdata.sources"
        ), f"{name} is bound to {mod}; hqdata.sources is forbidden"


# --------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------- #


def test_get_calendar_returns_open_dates_in_window(tmp_path):
    _build_snapshot(
        tmp_path,
        "tushare",
        [
            ("20240101", "N"),
            ("20240102", "Y"),
            ("20240103", "Y"),
            ("20240104", "Y"),
            ("20240105", "N"),
        ],
        daily={},
        lists={},
        factors={},
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    assert portal.get_calendar("20240102", "20240104") == [
        "20240102",
        "20240103",
        "20240104",
    ]


def test_is_trading_day_uses_csv(tmp_path):
    _build_snapshot(
        tmp_path,
        "tushare",
        [("20240102", "Y"), ("20240103", "N")],
        daily={},
        lists={},
        factors={},
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    assert portal.is_trading_day("20240102")
    assert not portal.is_trading_day("20240103")


def test_previous_and_next_trading_day(tmp_path):
    _build_snapshot(
        tmp_path,
        "tushare",
        [
            ("20240102", "Y"),
            ("20240103", "N"),
            ("20240104", "Y"),
            ("20240105", "Y"),
        ],
        daily={},
        lists={},
        factors={},
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    assert portal.previous_trading_day("20240105") == "20240104"
    assert portal.next_trading_day("20240102") == "20240104"
    # Skips non-open days.
    assert portal.previous_trading_day("20240104") == "20240102"
    assert portal.next_trading_day("20240104") == "20240105"


def test_calendar_raises_missing_when_csv_absent(tmp_path):
    (tmp_path / "tushare").mkdir()
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(MissingDataError):
        portal.get_calendar("20240101", "20240110")


def test_calendar_rejects_invalid_is_open(tmp_path):
    snap = tmp_path / "tushare"
    snap.mkdir()
    (snap / "calendar.csv").write_text("date,is_open\n20240102,X\n", encoding="utf-8")
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(InvalidDataError):
        portal.get_calendar("20240101", "20240110")


# --------------------------------------------------------------------- #
# Universe
# --------------------------------------------------------------------- #


def test_get_universe_returns_snapshot_for_exact_date(tmp_path):
    _build_snapshot(
        tmp_path,
        "tushare",
        [("20240102", "Y"), ("20240103", "Y")],
        daily={},
        lists={
            "20240102": [
                {
                    "symbol": "600000.SH",
                    "date": "20240102",
                    "name": "浦发",
                    "exchange": "SSE",
                    "board": "MB",
                    "curr_type": "CNY",
                    "list_date": "19991110",
                    "delist_date": "",
                },
                {
                    "symbol": "000001.SZ",
                    "date": "20240102",
                    "name": "平安",
                    "exchange": "SZE",
                    "board": "MB",
                    "curr_type": "CNY",
                    "list_date": "19910403",
                    "delist_date": "",
                },
            ],
            "20240103": [
                {
                    "symbol": "688001.SH",
                    "date": "20240103",
                    "name": "华润",
                    "exchange": "SSE",
                    "board": "STAR",
                    "curr_type": "CNY",
                    "list_date": "20190722",
                    "delist_date": "",
                },
            ],
        },
        factors={},
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    assert portal.get_universe("20240102") == ["000001.SZ", "600000.SH"]
    assert portal.get_universe("20240103") == ["688001.SH"]


def test_get_universe_does_not_fallback_when_snapshot_missing(tmp_path):
    """Missing `stock_list/{date}.csv` must raise, never fall back."""
    _build_snapshot(
        tmp_path,
        "tushare",
        [("20240102", "Y"), ("20240103", "Y")],
        daily={},
        lists={
            "20240102": [
                {
                    "symbol": "600000.SH",
                    "date": "20240102",
                    "name": "",
                    "exchange": "",
                    "board": "",
                    "curr_type": "",
                    "list_date": "",
                    "delist_date": "",
                }
            ]
        },
        factors={},
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(MissingDataError) as exc:
        portal.get_universe("20240103")
    assert "stock_list" in str(exc.value)


def test_get_universe_rejects_date_mismatch(tmp_path):
    """Filename date must equal CSV date column."""
    snap = tmp_path / "tushare"
    snap.mkdir()
    (snap / "stock_list").mkdir()
    (snap / "stock_list" / "20240102.csv").write_text(
        "symbol,date\n600000.SH,20240103\n", encoding="utf-8"
    )
    (snap / "calendar.csv").write_text("date,is_open\n20240102,Y\n", encoding="utf-8")
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(InvalidDataError):
        portal.get_universe("20240102")


def test_get_universe_rejects_duplicate_symbols(tmp_path):
    snap = tmp_path / "tushare"
    snap.mkdir()
    _write_calendar(snap, [("20240102", "Y")])
    (snap / "stock_list").mkdir()
    (snap / "stock_list" / "20240102.csv").write_text(
        "symbol,date\n600000.SH,20240102\n600000.SH,20240102\n",
        encoding="utf-8",
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(InvalidDataError, match="strictly ascending"):
        portal.get_universe("20240102")


# --------------------------------------------------------------------- #
# Bars
# --------------------------------------------------------------------- #


def _daily_row(symbol: str, date: str, ohlc=(10.0, 11.0, 9.0, 10.5)) -> dict:
    o, h, l, c = ohlc
    return {
        "symbol": symbol,
        "date": date,
        "pre_close": o,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000,
        "turnover": 10000.0,
        "change": c - o,
        "pct_change": (c - o) / o * 100,
    }


def test_get_bars_returns_bars_for_each_trading_day(tmp_path):
    _build_snapshot(
        tmp_path,
        "tushare",
        [("20240102", "Y"), ("20240103", "Y"), ("20240104", "Y")],
        daily={
            "20240102": [_daily_row("600000.SH", "20240102")],
            "20240103": [_daily_row("600000.SH", "20240103", (10.5, 11.5, 10.0, 11.2))],
            "20240104": [_daily_row("600000.SH", "20240104", (11.2, 12.0, 11.0, 11.8))],
        },
        lists={},
        factors={},
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    bars = portal.get_bars("600000.SH", "20240102", "20240104")
    assert [b.date for b in bars] == ["20240102", "20240103", "20240104"]
    assert bars[2].close == Decimal("11.8000")


def test_get_bars_preserves_csv_decimal_precision(tmp_path):
    snap = tmp_path / "tushare"
    snap.mkdir()
    _write_calendar(snap, [("20240102", "Y")])
    (snap / "stock_daily").mkdir()
    (snap / "stock_daily" / "20240102.csv").write_text(
        "symbol,date,open,high,low,close,volume\n"
        "600000.SH,20240102,10.123456789,11,9,10.987654321,1000\n",
        encoding="utf-8",
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    bars = portal.get_bars("600000.SH", "20240102", "20240102")
    assert bars[0].open == Decimal("10.1235")
    assert bars[0].close == Decimal("10.9877")


def test_get_bars_caches(tmp_path):
    _build_snapshot(
        tmp_path,
        "tushare",
        [("20240102", "Y")],
        daily={"20240102": [_daily_row("600000.SH", "20240102")]},
        lists={},
        factors={},
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    a = portal.get_bars("600000.SH", "20240102", "20240102")
    b = portal.get_bars("600000.SH", "20240102", "20240102")
    assert a is b  # served from cache


def test_get_bars_rejects_missing_daily_file(tmp_path):
    """A trading day without a daily file raises MissingDataError."""
    _build_snapshot(
        tmp_path,
        "tushare",
        [("20240102", "Y"), ("20240103", "Y")],
        daily={
            "20240102": [_daily_row("600000.SH", "20240102")],
            # 20240103 missing on purpose
        },
        lists={},
        factors={},
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(MissingDataError) as exc:
        portal.get_bars("600000.SH", "20240102", "20240103")
    assert "no bars" in str(exc.value).lower()


def test_get_bars_rejects_date_mismatch_in_daily(tmp_path):
    snap = tmp_path / "tushare"
    snap.mkdir()
    (snap / "stock_daily").mkdir()
    (snap / "stock_daily" / "20240102.csv").write_text(
        "symbol,date,open,high,low,close,volume\n600000.SH,20240103,10,11,9,10.5,1000\n",
        encoding="utf-8",
    )
    _write_calendar(snap, [("20240102", "Y")])
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(InvalidDataError):
        portal.get_bars("600000.SH", "20240102", "20240102")


def test_get_bars_rejects_wrong_symbol_row(tmp_path):
    snap = tmp_path / "tushare"
    snap.mkdir()
    (snap / "stock_daily").mkdir()
    (snap / "stock_daily" / "20240102.csv").write_text(
        "symbol,date,open,high,low,close,volume\n"
        "000001.SZ,20240102,10,11,9,10.5,1000\n",
        encoding="utf-8",
    )
    _write_calendar(snap, [("20240102", "Y")])
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(MissingDataError):
        # The file exists but has no row for 600000.SH.
        portal.get_bars("600000.SH", "20240102", "20240102")


def test_get_bars_rejects_duplicate_symbol_rows(tmp_path):
    snap = tmp_path / "tushare"
    snap.mkdir()
    (snap / "stock_daily").mkdir()
    (snap / "stock_daily" / "20240102.csv").write_text(
        "symbol,date,open,high,low,close,volume\n"
        "600000.SH,20240102,10,11,9,10.5,1000\n"
        "600000.SH,20240102,10,11,9,10.5,1000\n",
        encoding="utf-8",
    )
    _write_calendar(snap, [("20240102", "Y")])
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(InvalidDataError, match="at most one row"):
        portal.get_bars("600000.SH", "20240102", "20240102")


# --------------------------------------------------------------------- #
# Factor
# --------------------------------------------------------------------- #


def test_get_factor_returns_one_row_per_trading_day(tmp_path):
    _build_snapshot(
        tmp_path,
        "tushare",
        [("20240102", "Y"), ("20240103", "Y")],
        daily={},
        lists={},
        factors={
            "20240102": [
                {"symbol": "600000.SH", "date": "20240102", "factor": 1.0},
                {"symbol": "000001.SZ", "date": "20240102", "factor": 5.0},
            ],
            "20240103": [
                {"symbol": "600000.SH", "date": "20240103", "factor": 1.05},
                {"symbol": "000001.SZ", "date": "20240103", "factor": 5.10},
            ],
        },
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    rows = portal.get_factor("600000.SH", "20240102", "20240103")
    assert rows == [
        ("20240102", Decimal("1")),
        ("20240103", Decimal("1.05")),
    ]


def test_get_factor_preserves_csv_decimal_precision(tmp_path):
    snap = tmp_path / "tushare"
    snap.mkdir()
    _write_calendar(snap, [("20240102", "Y")])
    (snap / "stock_factor").mkdir()
    (snap / "stock_factor" / "20240102.csv").write_text(
        "symbol,date,factor\n600000.SH,20240102,1.123456789123456789\n",
        encoding="utf-8",
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    assert portal.get_factor("600000.SH", "20240102", "20240102") == [
        ("20240102", Decimal("1.123456789123456789"))
    ]


def test_get_factor_rejects_zero_factor(tmp_path):
    snap = tmp_path / "tushare"
    snap.mkdir()
    _write_calendar(snap, [("20240102", "Y")])
    (snap / "stock_factor").mkdir()
    (snap / "stock_factor" / "20240102.csv").write_text(
        "symbol,date,factor\n600000.SH,20240102,0\n", encoding="utf-8"
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(InvalidDataError):
        portal.get_factor("600000.SH", "20240102", "20240102")


def test_get_factor_rejects_date_mismatch(tmp_path):
    snap = tmp_path / "tushare"
    snap.mkdir()
    _write_calendar(snap, [("20240102", "Y")])
    (snap / "stock_factor").mkdir()
    (snap / "stock_factor" / "20240102.csv").write_text(
        "symbol,date,factor\n600000.SH,20240103,1.0\n", encoding="utf-8"
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    with pytest.raises(InvalidDataError):
        portal.get_factor("600000.SH", "20240102", "20240102")


# --------------------------------------------------------------------- #
# Cache isolation
# --------------------------------------------------------------------- #


def test_two_portals_with_different_data_roots_have_isolated_caches(tmp_path):
    snap = _build_snapshot(
        tmp_path,
        "tushare",
        [("20240102", "Y")],
        daily={"20240102": [_daily_row("600000.SH", "20240102")]},
        lists={},
        factors={},
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    # Move the snapshot to a different root.
    alt = tmp_path / "alt"
    alt.mkdir()
    (alt / "tushare").mkdir()
    for item in snap.iterdir():
        target = alt / "tushare" / item.name
        if item.is_dir():
            target.mkdir()
            for sub in item.rglob("*"):
                if sub.is_file():
                    rel = sub.relative_to(item)
                    target.joinpath(rel).parent.mkdir(parents=True, exist_ok=True)
                    target.joinpath(rel).write_bytes(sub.read_bytes())
        else:
            target.write_bytes(item.read_bytes())
    portal_alt = HqDataCsvPortal(source="tushare", data_root=str(alt))
    # The two portals point at equivalent snapshots but distinct roots;
    # their cache stores must be independent.
    assert portal.cache() is not portal_alt.cache()
    key_main = CacheKey(
        portal.data_root().as_posix(),
        portal.source_name(),
        "bars",
        "600000.SH",
        "",
        "20240102",
        "20240102",
    )
    key_alt = CacheKey(
        portal_alt.data_root().as_posix(),
        portal_alt.source_name(),
        "bars",
        "600000.SH",
        "",
        "20240102",
        "20240102",
    )
    assert key_main != key_alt


# --------------------------------------------------------------------- #
# Default data root
# --------------------------------------------------------------------- #


def test_default_data_root_is_hqdata_default():
    assert DEFAULT_DATA_ROOT == "~/.hqdata"


# --------------------------------------------------------------------- #
# Unused but kept to catch environment assumptions
# --------------------------------------------------------------------- #


def test_no_network_imports(monkeypatch):
    """The data layer must not import anything that opens a network socket."""
    forbidden = ("socket", "urllib", "urllib.request", "requests", "httpx")
    import hqbacktest.data.hqdata_portal as module

    for name in dir(module):
        if name.startswith("__"):
            continue
        attr = getattr(module, name)
        mod = getattr(attr, "__module__", "") or ""
        for f in forbidden:
            assert f not in mod, f"{name} pulls in {f}"


def test_os_environ_not_required_to_use(tmp_path, monkeypatch):
    """HQDATA_ROOT env var is optional; the constructor arg wins."""
    monkeypatch.delenv("HQDATA_ROOT", raising=False)
    snap = _build_snapshot(
        tmp_path,
        "tushare",
        [("20240102", "Y")],
        daily={"20240102": [_daily_row("600000.SH", "20240102")]},
        lists={},
        factors={},
    )
    portal = HqDataCsvPortal(source="tushare", data_root=str(tmp_path))
    assert portal.get_calendar("20240102", "20240102") == ["20240102"]
