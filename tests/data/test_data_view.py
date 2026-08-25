"""Tests for DataView's visibility enforcement."""

from decimal import Decimal

import pytest

from hqbacktest.data import (
    DataView,
    FutureDataAccessError,
    InMemoryDataPortal,
    InvalidDataError,
)
from hqbacktest.data.data_view import VALID_FIELDS
from hqbacktest.domain.bar import Bar


def _bar(date: str, close: str) -> Bar:
    return Bar.from_raw(
        symbol="600000.SH",
        date=date,
        open="10.00",
        high="11.00",
        low="9.00",
        close=close,
        volume=1000,
    )


def _portal() -> InMemoryDataPortal:
    p = InMemoryDataPortal(
        calendar=["20240102", "20240103", "20240104"],
        universe_by_date={"20240102": ["600000.SH"]},
    )
    p.add_bar(_bar("20240102", "10.00"))
    p.add_bar(_bar("20240103", "10.50"))
    p.add_bar(_bar("20240104", "10.80"))
    return p


def test_get_bars_within_visible_window():
    view = DataView(portal=_portal(), visible_through="20240103")
    bars = view.get_bars("600000.SH", "20240102", "20240103")
    assert [b.date for b in bars] == ["20240102", "20240103"]


def test_get_bars_rejects_future_end():
    view = DataView(portal=_portal(), visible_through="20240103")
    with pytest.raises(FutureDataAccessError):
        view.get_bars("600000.SH", "20240102", "20240104")


def test_get_bars_rejects_future_start():
    view = DataView(portal=_portal(), visible_through="20240103")
    with pytest.raises(FutureDataAccessError):
        view.get_bars("600000.SH", "20240104", "20240105")


def test_history_returns_last_n_bars():
    view = DataView(portal=_portal(), visible_through="20240104")
    closes = view.history("600000.SH", field="close", bar_count=2)
    assert [str(c) for c in closes] == ["10.5000", "10.8000"]


def test_history_caps_at_available_length():
    view = DataView(portal=_portal(), visible_through="20240103")
    closes = view.history("600000.SH", field="close", bar_count=10)
    assert len(closes) == 2


def test_history_respects_universe_start():
    view = DataView(
        portal=_portal(), visible_through="20240104", universe_start="20240103"
    )
    closes = view.history("600000.SH", field="close", bar_count=10)
    assert [str(c) for c in closes] == ["10.5000", "10.8000"]


def test_history_rejects_unknown_field():
    view = DataView(portal=_portal(), visible_through="20240104")
    with pytest.raises(ValueError):
        view.history("600000.SH", field="not_a_field", bar_count=1)


def test_history_rejects_non_positive_bar_count():
    view = DataView(portal=_portal(), visible_through="20240104")
    with pytest.raises(ValueError):
        view.history("600000.SH", field="close", bar_count=0)


@pytest.mark.parametrize("field", VALID_FIELDS)
def test_history_supports_every_bar_field(field):
    view = DataView(portal=_portal(), visible_through="20240104")
    out = view.history("600000.SH", field=field, bar_count=1)
    assert len(out) == 1


def test_current_price_returns_latest_close():
    view = DataView(portal=_portal(), visible_through="20240104")
    assert view.current_price("600000.SH") == Decimal("10.8000")


def test_current_price_returns_none_when_no_data():
    empty = InMemoryDataPortal(calendar=["20240102"])
    view = DataView(portal=empty, visible_through="20240102")
    assert view.current_price("600000.SH") is None


def test_current_price_does_not_hide_data_validation_error():
    class BrokenPortal(InMemoryDataPortal):
        def get_bars(self, symbol, start, end):
            raise InvalidDataError("bars", "malformed source row")

    # The portal must have at least one calendar day for the lookback
    # window to actually invoke get_bars (else current_price returns
    # None without touching the data layer).
    view = DataView(
        portal=BrokenPortal(calendar=["20240102"]),
        visible_through="20240102",
    )
    with pytest.raises(InvalidDataError, match="malformed"):
        view.current_price("600000.SH")


def test_universe_uses_visible_through():
    view = DataView(portal=_portal(), visible_through="20240102")
    assert view.universe() == ["600000.SH"]


def test_universe_rejects_future_date():
    view = DataView(portal=_portal(), visible_through="20240103")
    with pytest.raises(FutureDataAccessError):
        view.get_universe(date="20240105")


def test_constructor_rejects_universe_start_after_visible_through():
    with pytest.raises(FutureDataAccessError):
        DataView(
            portal=_portal(),
            visible_through="20240102",
            universe_start="20240105",
        )
