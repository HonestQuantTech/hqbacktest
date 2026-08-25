"""Tests for the order-intent helper functions."""

from decimal import Decimal

import pytest

from hqbacktest.engine.errors import StrategyLifecycleError
from hqbacktest.engine.intents import (
    normalize_percent,
    quantity_from_value,
    side_from_quantity,
    signed_diff_to_lots,
    target_quantity_for_value,
    target_value_for_percent,
)


def test_quantity_from_value_buy_rounds_to_lots():
    # 1000 CNY @ 12.50 / share = 80 shares; rounded down to lot = 0.
    # 10000 CNY @ 12.50 = 800 shares = 8 lots.
    assert quantity_from_value(Decimal("10000"), Decimal("12.50")) == 800


def test_quantity_from_value_sell_is_negative():
    # -10000 / 12.50 = -800 (8 lots SELL).
    assert quantity_from_value(Decimal("-10000"), Decimal("12.50")) == -800


def test_quantity_from_value_zero_returns_zero():
    assert quantity_from_value(Decimal("0"), Decimal("10")) == 0


def test_quantity_from_value_rejects_non_decimal():
    with pytest.raises(StrategyLifecycleError):
        quantity_from_value("1000", Decimal("10"))  # type: ignore[arg-type]


def test_quantity_from_value_rejects_non_positive_price():
    with pytest.raises(StrategyLifecycleError):
        quantity_from_value(Decimal("1000"), Decimal("0"))


def test_side_from_quantity():
    assert side_from_quantity(100) is not None
    assert side_from_quantity(-100) is not None
    assert side_from_quantity(0) is None


def test_signed_diff_to_lots_buy():
    # Need to go from 0 to 250 shares; rounds down to 200 (2 lots).
    assert signed_diff_to_lots(250, 0) == 200


def test_signed_diff_to_lots_sell():
    # Need to go from 300 to 50; diff = -250. Task 16: SELL preserves
    # the requested share count (odd-lot SELLs allowed per A-share rules),
    # so the result is -250, not lot-rounded -200.
    assert signed_diff_to_lots(50, 300) == -250


def test_signed_diff_to_lots_no_change():
    assert signed_diff_to_lots(200, 200) == 0


def test_target_quantity_for_value_positive():
    # target = 12500 / 12.50 = 1000 shares.
    assert (
        target_quantity_for_value(
            Decimal("12500"), Decimal("12.50"), current_quantity=0
        )
        == 1000
    )


def test_target_quantity_for_value_zero_returns_zero_per_docstring():
    # Task 16: per the function's docstring ("may be 0 to flatten"), a
    # zero target returns 0 — the caller flattens via order_target().
    assert (
        target_quantity_for_value(Decimal("0"), Decimal("12.50"), current_quantity=300)
        == 0
    )


def test_target_quantity_for_value_rejects_negative():
    with pytest.raises(StrategyLifecycleError):
        target_quantity_for_value(Decimal("-1"), Decimal("10"), current_quantity=0)


def test_target_value_for_percent_normal_range():
    equity = Decimal("100000")
    assert target_value_for_percent(Decimal("0.25"), equity) == Decimal("25000.00")
    assert target_value_for_percent(Decimal("1"), equity) == Decimal("100000.00")
    assert target_value_for_percent(Decimal("0"), equity) == Decimal("0.00")


def test_target_value_for_percent_rejects_out_of_range():
    with pytest.raises(StrategyLifecycleError):
        target_value_for_percent(Decimal("1.1"), Decimal("1000"))
    with pytest.raises(StrategyLifecycleError):
        target_value_for_percent(Decimal("-0.1"), Decimal("1000"))


def test_normalize_percent_accepts_decimal_str_int():
    assert normalize_percent(Decimal("0.5")) == Decimal("0.5")
    assert normalize_percent("0.5") == Decimal("0.5")
    assert normalize_percent(0) == Decimal(0)


def test_normalize_percent_rejects_float():
    with pytest.raises(StrategyLifecycleError):
        normalize_percent(0.5)


def test_normalize_percent_rejects_garbage():
    with pytest.raises(StrategyLifecycleError):
        normalize_percent("not-a-number")
