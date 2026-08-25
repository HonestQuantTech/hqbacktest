"""Tests for the data-layer validators."""

import pandas as pd
import pytest

from hqbacktest.data.errors import InvalidDataError
from hqbacktest.data.validators import (
    assert_unique_sorted,
    require_columns,
    validate_decimal_series,
    validate_symbol,
    validate_yyyymmdd,
)


@pytest.mark.parametrize(
    "value",
    ["20240102", "19900101", "20991231"],
)
def test_validate_yyyymmdd_accepts_valid(value):
    assert validate_yyyymmdd(value) == value


@pytest.mark.parametrize(
    "value",
    ["2024-01-02", "240102", "abcdefgh", ""],
)
def test_validate_yyyymmdd_rejects_bad(value):
    with pytest.raises(InvalidDataError):
        validate_yyyymmdd(value)


@pytest.mark.parametrize(
    "value",
    [
        # Task 22.3: 8-digit but NOT a real calendar date (e.g. month 13,
        # day 32, day 30 of February). Previously these slipped through
        # because the validator only checked `len == 8 and isdigit()`.
        "20241399",  # month 13
        "20240132",  # January 32nd
        "20240230",  # Feb 30 in a non-leap year
        "20230229",  # Feb 29 in a non-leap year (2023 is not a leap year)
        "20250431",  # April only has 30 days
    ],
)
def test_validate_yyyymmdd_rejects_impossible_calendar_dates(value):
    """Task 22.3: 8-digit strings that are NOT real calendar dates
    must be rejected by `validate_yyyymmdd` itself, not by an
    unrelated check downstream.
    """
    with pytest.raises(InvalidDataError, match="calendar"):
        validate_yyyymmdd(value)


def test_validate_yyyymmdd_accepts_first_day_sentinel():
    """The first-trading-day sentinel ``"00000000"`` must still pass.

    ``datetime.strptime("00000000", "%Y%m%d")`` raises ``ValueError``
    (year 0 is disallowed in Python ≥ 3), so `validate_yyyymmdd`
    special-cases it before the calendar check. `DataView` / `Scheduler`
    use it to mean "no data visible yet" on the very first trading day.
    """
    assert validate_yyyymmdd("00000000") == "00000000"


@pytest.mark.parametrize(
    "value",
    [
        "20200229",  # leap year Feb 29
        "20000229",  # century leap year Feb 29
        "20240131",  # 31-day month last day
        "20240430",  # 30-day month last day
    ],
)
def test_validate_yyyymmdd_accepts_real_calendar_dates(value):
    """Task 22.3: real-but-edge-case calendar dates must still pass."""
    assert validate_yyyymmdd(value) == value


@pytest.mark.parametrize("value", [None, 20240102, ["20240102"]])
def test_validate_yyyymmdd_rejects_non_string(value):
    with pytest.raises(InvalidDataError):
        validate_yyyymmdd(value)


@pytest.mark.parametrize(
    "value",
    ["600000.SH", "000001.SZ", "688001.SH", "830001.BJ"],
)
def test_validate_symbol_accepts_supported_suffixes(value):
    assert validate_symbol(value) == value


@pytest.mark.parametrize(
    "value",
    ["600000", "600000.HK", "abcdef.SH", "600000.SH ", " 600000.SH", ""],
)
def test_validate_symbol_rejects_bad(value):
    with pytest.raises(InvalidDataError):
        validate_symbol(value)


def test_validate_symbol_rejects_non_string():
    with pytest.raises(InvalidDataError):
        validate_symbol(12345)


def test_assert_unique_sorted_accepts_strictly_ascending():
    assert_unique_sorted([1, 2, 3], name="x")


def test_assert_unique_sorted_rejects_equal():
    with pytest.raises(InvalidDataError):
        assert_unique_sorted([1, 1, 2], name="x")


def test_assert_unique_sorted_rejects_descending():
    with pytest.raises(InvalidDataError):
        assert_unique_sorted([2, 1], name="x")


def test_require_columns_lists_missing():
    df = pd.DataFrame({"a": [1], "b": [2]})
    with pytest.raises(InvalidDataError) as exc:
        require_columns(df, ["a", "c"], name="test")
    assert "c" in str(exc.value)


def test_validate_decimal_series_rejects_nan():
    s = pd.Series([1.0, float("nan"), 2.0])
    with pytest.raises(InvalidDataError):
        validate_decimal_series(s, name="x")


def test_validate_decimal_series_rejects_non_positive():
    s = pd.Series([1.0, -0.5, 2.0])
    with pytest.raises(InvalidDataError):
        validate_decimal_series(s, name="x")


def test_validate_decimal_series_accepts_positive():
    s = pd.Series([1.0, 2.0, 3.5])
    validate_decimal_series(s, name="x")
