"""Tests for TradingDayIterator."""

import pytest

from hqbacktest.data import InMemoryDataPortal
from hqbacktest.engine.errors import ConfigurationError
from hqbacktest.engine.iterator import TradingDayIterator


def _portal_with(days):
    p = InMemoryDataPortal(calendar=days)
    return p


def test_iterator_yields_each_trading_day():
    p = _portal_with(["20240102", "20240103", "20240104"])
    it = TradingDayIterator(p, "20240102", "20240104")
    assert list(it) == ["20240102", "20240103", "20240104"]


def test_iterator_respects_window():
    p = _portal_with(["20240101", "20240102", "20240103", "20240104", "20240105"])
    it = TradingDayIterator(p, "20240103", "20240104")
    assert list(it) == ["20240103", "20240104"]


def test_iterator_raises_when_window_has_no_open_days():
    """Task 20: an empty trading-day window is a hard error, not a
    silent success. This avoids the "no signals" misreport bug.
    """
    p = _portal_with(["20240102"])
    with pytest.raises(ConfigurationError, match="no trading days"):
        TradingDayIterator(p, "20240105", "20240110")


def test_iterator_rejects_invalid_dates():
    p = _portal_with(["20240102"])
    with pytest.raises(Exception):
        TradingDayIterator(p, "2024-01-02", "20240110")


def test_iterator_rejects_inverted_window():
    p = _portal_with(["20240102"])
    with pytest.raises(ConfigurationError):
        TradingDayIterator(p, "20240110", "20240102")


def test_iterator_supports_multiple_iteration_passes():
    p = _portal_with(["20240102", "20240103"])
    it = TradingDayIterator(p, "20240102", "20240103")
    first = list(it)
    second = list(it)
    assert first == second == ["20240102", "20240103"]


def test_iterator_len_and_is_empty():
    p = _portal_with(["20240102", "20240103"])
    it = TradingDayIterator(p, "20240102", "20240103")
    assert len(it) == 2
    assert not it.is_empty()


def test_iterator_does_not_invent_natural_days():
    """If the calendar lacks a date, the iterator must not yield it."""
    p = _portal_with(["20240102", "20240104"])  # 20240103 is a non-trading day
    it = TradingDayIterator(p, "20240101", "20240110")
    days = list(it)
    assert "20240103" not in days
    assert days == ["20240102", "20240104"]
