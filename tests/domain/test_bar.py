"""Tests for the Bar domain model."""

from decimal import Decimal

import pytest

from hqbacktest.domain.bar import Bar


def _make_bar(**overrides):
    defaults = dict(
        symbol="600000.SH",
        date="20240102",
        open=Decimal("10.00"),
        high=Decimal("10.50"),
        low=Decimal("9.90"),
        close=Decimal("10.20"),
        volume=1000,
    )
    defaults.update(overrides)
    return Bar(**defaults)


def test_bar_constructs_with_valid_ohlc():
    bar = _make_bar()
    assert bar.symbol == "600000.SH"
    assert bar.date == "20240102"
    assert bar.close == Decimal("10.20")


def test_bar_rejects_invalid_date():
    with pytest.raises(ValueError):
        _make_bar(date="2024-01-02")
    with pytest.raises(ValueError):
        _make_bar(date="240102")
    with pytest.raises(ValueError):
        _make_bar(date="abcdefgh")


def test_bar_rejects_zero_or_negative_prices():
    with pytest.raises(ValueError):
        _make_bar(close=Decimal("0"))
    with pytest.raises(ValueError):
        _make_bar(open=Decimal("-1"))


def test_bar_rejects_non_decimal_prices():
    with pytest.raises(ValueError):
        _make_bar(open=10.0)


def test_bar_rejects_low_above_high():
    with pytest.raises(ValueError):
        _make_bar(low=Decimal("11"), high=Decimal("10"))


def test_bar_rejects_open_outside_range():
    with pytest.raises(ValueError):
        _make_bar(open=Decimal("9"), high=Decimal("10"), low=Decimal("9.5"))
    with pytest.raises(ValueError):
        _make_bar(open=Decimal("11"), high=Decimal("10.5"), low=Decimal("10"))


def test_bar_from_raw_quantizes_prices():
    bar = Bar.from_raw(
        symbol="600000.SH",
        date="20240102",
        open="10.12345",
        high="10.98765",
        low="9.87654",
        close="10.55555",
        volume=2000,
    )
    # ROUND_HALF_EVEN: kept digit decides; 5 rounds to the nearest even.
    assert bar.open == Decimal("10.1234")  # kept 4 is even -> stays 4
    assert bar.high == Decimal("10.9876")  # kept 6 is even -> stays 6
    assert bar.low == Decimal("9.8765")  # digit 4 < 5 -> stays
    assert bar.close == Decimal("10.5556")  # kept 5 is odd -> rounds up to 6
    assert bar.volume == 2000


def test_bar_rejects_negative_volume():
    with pytest.raises(ValueError):
        _make_bar(volume=-1)


def test_bar_is_immutable():
    bar = _make_bar()
    with pytest.raises(Exception):
        bar.close = Decimal("99")  # type: ignore[misc]
