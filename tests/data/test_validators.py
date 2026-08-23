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
