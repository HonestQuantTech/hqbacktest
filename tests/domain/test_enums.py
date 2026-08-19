"""Tests for the closed enums in hqbacktest.domain.enums."""

from hqbacktest.domain.enums import (
    EventType,
    OrderStatus,
    OrderType,
    PriceMode,
    RejectReason,
    Side,
)


def test_side_is_closed():
    assert Side.BUY.value == "BUY"
    assert Side.SELL.value == "SELL"
    assert {member.name for member in Side} == {"BUY", "SELL"}


def test_order_type_only_market_in_v01():
    assert {member.name for member in OrderType} == {"MARKET"}


def test_order_status_contains_terminal_states():
    terminal = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}
    assert terminal.issubset(set(OrderStatus))


def test_reject_reason_is_closed_and_known():
    expected = {
        "INSUFFICIENT_CASH",
        "INSUFFICIENT_SHARES",
        "INVALID_PRICE",
        "NO_OPEN_PRICE",
        "SUSPENDED",
        "UNKNOWN_SYMBOL",
        "NOT_TRADABLE",
        "UNSUPPORTED_ORDER_TYPE",
        "MISSING_DATA",
        "DUPLICATE_ORDER",
        "BACKTEST_ENDED",
        "OTHER",
    }
    assert {member.name for member in RejectReason} == expected


def test_event_type_includes_phases_and_order_events():
    names = {member.name for member in EventType}
    for required in {
        "SESSION_START",
        "BEFORE_TRADING_START",
        "OPEN_MATCH",
        "BAR_CLOSE",
        "AFTER_TRADING_END",
        "ORDER_CREATED",
        "ORDER_ACCEPTED",
        "ORDER_REJECTED",
        "ORDER_CANCELLED",
        "ORDER_FILLED",
    }:
        assert required in names


def test_price_mode_open_only():
    assert {member.name for member in PriceMode} == {"OPEN"}
