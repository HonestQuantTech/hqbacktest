"""CSV smoke tests against the local `~/.hqdata/{source}` snapshot.

These tests read **only** the CSV files dropped by the hqdata CLI. They never
call hqdata, hit the network, or read tokens. Each test is auto-skipped when
the corresponding snapshot directory does not exist or is empty.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hqbacktest.data import HqDataCsvPortal


def _data_root() -> Path:
    raw = os.environ.get("HQDATA_ROOT", "~/.hqdata")
    return Path(raw).expanduser()


def _source_available(name: str) -> bool:
    """True iff the source's CSV directory exists and looks non-empty."""
    root = _data_root() / name
    if not root.exists() or not root.is_dir():
        return False
    if not (root / "calendar.csv").exists():
        return False
    try:
        next(root.iterdir())
    except StopIteration:
        return False
    return True


def _window(portal: HqDataCsvPortal, n_days: int = 5):
    """Pick the first `n_days` open trading days from the portal calendar."""
    end = portal.data_version().as_of
    # Walk back `n_days` from end.
    calendar = portal.get_calendar("20260101", end)
    if len(calendar) < n_days:
        return calendar
    return calendar[-n_days:]


# --------------------------------------------------------------------- #
# tushare
# --------------------------------------------------------------------- #


@pytest.mark.integration
def test_integration_tushare_csv_loads_calendar():
    if not _source_available("tushare"):
        pytest.skip("tushare CSV snapshot not available at ~/.hqdata/tushare")
    portal = HqDataCsvPortal(source="tushare", data_root=str(_data_root()))
    window = _window(portal)
    calendar = portal.get_calendar(window[0], window[-1])
    assert calendar, "tushare calendar should not be empty"
    assert all(len(d) == 8 and d.isdigit() for d in calendar)


@pytest.mark.integration
def test_integration_tushare_csv_get_universe():
    if not _source_available("tushare"):
        pytest.skip("tushare CSV snapshot not available at ~/.hqdata/tushare")
    portal = HqDataCsvPortal(source="tushare", data_root=str(_data_root()))
    window = _window(portal)
    universe = portal.get_universe(window[-1])
    assert universe, "universe should not be empty"


@pytest.mark.integration
def test_integration_tushare_csv_get_bars_and_factor():
    if not _source_available("tushare"):
        pytest.skip("tushare CSV snapshot not available at ~/.hqdata/tushare")
    portal = HqDataCsvPortal(source="tushare", data_root=str(_data_root()))
    window = _window(portal, n_days=3)
    start, end = window[0], window[-1]
    universe = portal.get_universe(end)
    assert universe, "tushare universe should not be empty"
    symbol = universe[0]
    bars = portal.get_bars(symbol, start, end)
    assert bars, f"bars for {symbol} should not be empty"
    rows = portal.get_factor(symbol, start, end)
    assert rows, f"factor for {symbol} should not be empty"
    assert all(factor > 0 for _, factor in rows)


# --------------------------------------------------------------------- #
# ricequant
# --------------------------------------------------------------------- #


@pytest.mark.integration
def test_integration_ricequant_csv_loads_calendar():
    if not _source_available("ricequant"):
        pytest.skip("ricequant CSV snapshot not available at ~/.hqdata/ricequant")
    portal = HqDataCsvPortal(source="ricequant", data_root=str(_data_root()))
    window = _window(portal)
    calendar = portal.get_calendar(window[0], window[-1])
    assert calendar


@pytest.mark.integration
def test_integration_ricequant_csv_get_universe():
    if not _source_available("ricequant"):
        pytest.skip("ricequant CSV snapshot not available at ~/.hqdata/ricequant")
    portal = HqDataCsvPortal(source="ricequant", data_root=str(_data_root()))
    window = _window(portal)
    universe = portal.get_universe(window[-1])
    assert universe
