"""Tests for the Order domain model and its lifecycle."""

from decimal import Decimal

import pytest

from hqbacktest.domain.enums import (
    EventType,
    OrderStatus,
    OrderType,
    RejectReason,
    Side,
)
from hqbacktest.domain.order import Order


def _new_order(**overrides) -> Order:
    defaults = dict(
        order_id="O001",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=100,
        order_type=OrderType.MARKET,
        created_at="20240102",
        created_session=EventType.BEFORE_TRADING_START,
    )
    defaults.update(overrides)
    return Order(**defaults)


def test_order_default_state_is_new():
    o = _new_order()
    assert o.status is OrderStatus.NEW
    assert o.filled_quantity == 0
    assert o.avg_fill_price is None
    assert not o.is_terminal()


def test_order_rejects_non_positive_quantity():
    with pytest.raises(ValueError):
        _new_order(quantity=0)
    with pytest.raises(ValueError):
        _new_order(quantity=-1)


def test_order_transition_stamps_timestamps():
    o = _new_order()
    o.transition(OrderStatus.ACCEPTED, at="20240102")
    assert o.accepted_at == "20240102"
    o.transition(OrderStatus.PENDING, at="20240102")
    assert o.pending_at == "20240102"
    o.transition(OrderStatus.FILLED, at="20240103")
    assert o.filled_at == "20240103"
    assert o.is_terminal()


def test_illegal_transition_raises_and_keeps_state():
    o = _new_order()
    o.transition(OrderStatus.ACCEPTED, at="20240102")
    o.transition(OrderStatus.PENDING, at="20240102")
    with pytest.raises(Exception):
        o.transition(OrderStatus.NEW, at="20240103")
    assert o.status is OrderStatus.PENDING


def test_order_rejection_records_reason_and_detail():
    o = _new_order()
    o.transition(
        OrderStatus.REJECTED,
        at="20240102",
        reason=RejectReason.INSUFFICIENT_CASH,
        detail="need 1500.00, have 1000.00",
    )
    assert o.status is OrderStatus.REJECTED
    assert o.reject_reason is RejectReason.INSUFFICIENT_CASH
    assert "1500.00" in (o.reject_detail or "")


def test_record_full_fill_moves_to_filled():
    o = _new_order()
    o.transition(OrderStatus.ACCEPTED, at="20240102")
    o.transition(OrderStatus.PENDING, at="20240102")
    o.record_fill("F001", quantity=100, price=Decimal("10.50"), at="20240103")
    assert o.filled_quantity == 100
    assert o.avg_fill_price == Decimal("10.5000")
    assert o.fill_ids == ["F001"]
    assert o.status is OrderStatus.FILLED


def test_record_partial_fill_then_full_fill():
    o = _new_order(quantity=200)
    o.transition(OrderStatus.ACCEPTED, at="20240102")
    o.transition(OrderStatus.PENDING, at="20240102")
    o.record_fill("F001", quantity=100, price=Decimal("10.00"), at="20240103")
    assert o.status is OrderStatus.PARTIALLY_FILLED
    assert o.filled_quantity == 100
    o.record_fill("F002", quantity=100, price=Decimal("12.00"), at="20240103")
    assert o.status is OrderStatus.FILLED
    assert o.avg_fill_price == Decimal("11.0000")  # weighted avg of 10 and 12


def test_record_fill_rejects_overflow():
    o = _new_order(quantity=100)
    o.transition(OrderStatus.ACCEPTED, at="20240102")
    o.transition(OrderStatus.PENDING, at="20240102")
    with pytest.raises(ValueError):
        o.record_fill("F001", quantity=150, price=Decimal("10"), at="20240103")


def test_record_fill_rejects_non_decimal_price():
    o = _new_order()
    o.transition(OrderStatus.ACCEPTED, at="20240102")
    o.transition(OrderStatus.PENDING, at="20240102")
    with pytest.raises(ValueError):
        o.record_fill("F001", quantity=50, price=10.0, at="20240103")  # type: ignore[arg-type]


def test_record_fill_for_new_order_does_not_mutate_order():
    o = _new_order()
    with pytest.raises(ValueError, match="cannot record a fill"):
        o.record_fill("F001", quantity=100, price=Decimal("10"), at="20240103")
    assert o.status is OrderStatus.NEW
    assert o.filled_quantity == 0
    assert o.avg_fill_price is None
    assert o.fill_ids == []


def test_multiple_partial_fills_keep_partial_status_until_complete():
    o = _new_order(quantity=300)
    o.transition(OrderStatus.ACCEPTED, at="20240102")
    o.transition(OrderStatus.PENDING, at="20240102")
    o.record_fill("F001", quantity=100, price=Decimal("10"), at="20240103")
    o.record_fill("F002", quantity=100, price=Decimal("11"), at="20240103")
    assert o.status is OrderStatus.PARTIALLY_FILLED
    assert o.partially_filled_at == "20240103"
    assert o.filled_quantity == 200
    assert o.avg_fill_price == Decimal("10.5000")


def test_order_rejects_invalid_created_date_and_enum_values():
    with pytest.raises(ValueError, match="created_at"):
        _new_order(created_at="2024-01-02")
    with pytest.raises(ValueError, match="side"):
        _new_order(side="BUY")  # type: ignore[arg-type]


def test_remaining_and_terminal_helpers():
    o = _new_order(quantity=100)
    o.transition(OrderStatus.ACCEPTED, at="20240102")
    o.transition(OrderStatus.CANCELLED, at="20240102")
    assert o.remaining() == 100
    assert o.is_terminal()
