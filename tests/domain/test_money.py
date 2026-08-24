"""Tests for hqbacktest.domain.money helpers."""

from decimal import Decimal

import pytest

from hqbacktest.domain.money import (
    CASH_QUANT,
    LOT_SIZE,
    PRICE_QUANT,
    MoneyError,
    cash_for_trade,
    is_non_negative,
    is_positive,
    quantize_cash,
    quantize_price,
    round_lot,
    to_decimal,
)


def test_to_decimal_accepts_int_str_decimal():
    assert to_decimal("1.23") == Decimal("1.23")
    assert to_decimal(42) == Decimal(42)
    assert to_decimal(Decimal("0.5")) == Decimal("0.5")


def test_to_decimal_rejects_float():
    with pytest.raises(MoneyError):
        to_decimal(0.1)


def test_to_decimal_rejects_invalid_string():
    with pytest.raises(MoneyError):
        to_decimal("not-a-number")


def test_to_decimal_rejects_bool():
    with pytest.raises(MoneyError):
        to_decimal(True)


def test_quantize_cash_rounds_half_even():
    assert quantize_cash("1.005") == Decimal("1.00")
    assert quantize_cash("1.015") == Decimal("1.02")
    assert quantize_cash("1.025") == Decimal("1.02")
    assert CASH_QUANT == Decimal("0.01")


def test_quantize_price_has_four_decimals():
    assert quantize_price("10.123456") == Decimal("10.1235")
    assert PRICE_QUANT == Decimal("0.0001")


def test_is_positive_and_non_negative():
    assert is_positive("0.01")
    assert not is_positive("0")
    assert not is_positive("-1")
    assert is_non_negative("0")
    assert not is_non_negative("-0.01")


def test_round_lot_drops_to_lot_boundary():
    assert round_lot(150) == 100
    assert round_lot(199) == 100
    assert round_lot(200) == 200
    assert round_lot(0) == 0
    assert round_lot(-50) == 0
    assert round_lot(250, lot_size=50) == 250


def test_cash_for_trade_quantizes_to_cash():
    assert cash_for_trade(100, "12.345") == Decimal("1234.50")


def test_cash_for_trade_rejects_non_positive_quantity():
    with pytest.raises(MoneyError):
        cash_for_trade(0, "10")
    with pytest.raises(MoneyError):
        cash_for_trade(-1, "10")
