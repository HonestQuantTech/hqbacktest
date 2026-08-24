"""Tests for the Fill domain model."""

from decimal import Decimal

import pytest

from hqbacktest.domain.enums import EventType, Side
from hqbacktest.domain.fill import Fill


def test_buy_fill_carries_positive_amount():
    fill = Fill.from_trade(
        fill_id="F001",
        order_id="O001",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=100,
        price=Decimal("12.50"),
        commission=Decimal("5.00"),
        stamp_tax=Decimal("0"),
        other_fee=Decimal("0"),
        filled_at="20240103",
        session=EventType.OPEN_MATCH,
    )
    assert fill.amount == Decimal("1250.00")
    assert fill.total_fee() == Decimal("5.00")
    assert fill.net_amount() == Decimal("-1255.00")  # cash out


def test_sell_fill_carries_negative_amount_and_pays_stamp_tax():
    fill = Fill.from_trade(
        fill_id="F002",
        order_id="O002",
        symbol="600000.SH",
        side=Side.SELL,
        quantity=100,
        price=Decimal("13.00"),
        commission=Decimal("5.00"),
        stamp_tax=Decimal("6.50"),
        other_fee=Decimal("0"),
        filled_at="20240104",
        session=EventType.OPEN_MATCH,
    )
    assert fill.amount == Decimal("-1300.00")
    assert fill.net_amount() == Decimal("1288.50")  # 1300 - 5 - 6.50


def test_fill_rejects_sign_mismatch():
    with pytest.raises(ValueError):
        Fill(
            fill_id="F003",
            order_id="O003",
            symbol="600000.SH",
            side=Side.BUY,
            quantity=100,
            price=Decimal("10"),
            amount=Decimal("-1000"),  # wrong sign
            commission=Decimal("0"),
            stamp_tax=Decimal("0"),
            other_fee=Decimal("0"),
            filled_at="20240103",
            session=EventType.OPEN_MATCH,
        )


def test_fill_rejects_amount_that_does_not_match_quantity_and_price():
    with pytest.raises(ValueError, match="signed gross"):
        Fill(
            fill_id="F003",
            order_id="O003",
            symbol="600000.SH",
            side=Side.BUY,
            quantity=100,
            price=Decimal("10.0000"),
            amount=Decimal("999.00"),
            commission=Decimal("0"),
            stamp_tax=Decimal("0"),
            other_fee=Decimal("0"),
            filled_at="20240103",
            session=EventType.OPEN_MATCH,
        )


def test_fill_rejects_money_with_invalid_scale():
    with pytest.raises(ValueError, match="commission must be quantized"):
        Fill(
            fill_id="F003",
            order_id="O003",
            symbol="600000.SH",
            side=Side.BUY,
            quantity=100,
            price=Decimal("10.0000"),
            amount=Decimal("1000.00"),
            commission=Decimal("0.001"),
            stamp_tax=Decimal("0"),
            other_fee=Decimal("0"),
            filled_at="20240103",
            session=EventType.OPEN_MATCH,
        )


def test_fill_rejects_non_decimal_money():
    with pytest.raises(ValueError):
        Fill(
            fill_id="F004",
            order_id="O004",
            symbol="600000.SH",
            side=Side.BUY,
            quantity=100,
            price=10.0,
            amount=Decimal("1000"),
            commission=Decimal("0"),
            stamp_tax=Decimal("0"),
            other_fee=Decimal("0"),
            filled_at="20240103",
            session=EventType.OPEN_MATCH,
        )


def test_fill_rejects_bad_date():
    with pytest.raises(ValueError):
        Fill.from_trade(
            fill_id="F005",
            order_id="O005",
            symbol="600000.SH",
            side=Side.BUY,
            quantity=100,
            price=Decimal("10"),
            commission=Decimal("0"),
            stamp_tax=Decimal("0"),
            other_fee=Decimal("0"),
            filled_at="2024-01-03",
            session=EventType.OPEN_MATCH,
        )


def test_fill_rejects_negative_fee():
    with pytest.raises(ValueError):
        Fill.from_trade(
            fill_id="F006",
            order_id="O006",
            symbol="600000.SH",
            side=Side.BUY,
            quantity=100,
            price=Decimal("10"),
            commission=Decimal("-1"),
            stamp_tax=Decimal("0"),
            other_fee=Decimal("0"),
            filled_at="20240103",
            session=EventType.OPEN_MATCH,
        )


def test_fill_is_immutable():
    fill = Fill.from_trade(
        fill_id="F007",
        order_id="O007",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=100,
        price=Decimal("10"),
        commission=Decimal("0"),
        stamp_tax=Decimal("0"),
        other_fee=Decimal("0"),
        filled_at="20240103",
        session=EventType.OPEN_MATCH,
    )
    with pytest.raises(Exception):
        fill.price = Decimal("20")  # type: ignore[misc]
