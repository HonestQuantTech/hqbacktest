"""Tests for the Portfolio ledger and buy/sell round trips."""

from decimal import Decimal

import pytest

from hqbacktest.domain.enums import EventType, Side
from hqbacktest.domain.fill import Fill
from hqbacktest.domain.portfolio import Portfolio


def test_initial_portfolio_mirrors_initial_cash():
    p = Portfolio(initial_cash=Decimal("100000.00"))
    assert p.cash == Decimal("100000.00")
    assert p.frozen_cash == Decimal(0)
    assert p.realized_pnl == Decimal(0)
    assert dict(p.positions) == {}


def test_portfolio_rejects_float_initial_cash():
    with pytest.raises(ValueError):
        Portfolio(initial_cash=100000.0)  # type: ignore[arg-type]


def test_portfolio_honors_explicit_zero_cash():
    p = Portfolio(initial_cash=Decimal("100000.00"), cash=Decimal("0"))
    assert p.cash == Decimal("0")


def test_reserve_and_release_cash():
    p = Portfolio(initial_cash=Decimal("10000.00"))
    p.reserve_cash(Decimal("3000.00"))
    assert p.frozen_cash == Decimal("3000.00")
    p.release_cash(Decimal("1000.00"))
    assert p.frozen_cash == Decimal("2000.00")


def test_reserve_more_than_available_raises():
    p = Portfolio(initial_cash=Decimal("1000.00"))
    with pytest.raises(ValueError):
        p.reserve_cash(Decimal("2000.00"))


def test_buy_then_sell_round_trip_preserves_ledger():
    p = Portfolio(initial_cash=Decimal("10000.00"))

    buy = Fill.from_trade(
        fill_id="F001",
        order_id="O001",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=100,
        price=Decimal("10.00"),
        commission=Decimal("5.00"),
        stamp_tax=Decimal("0"),
        other_fee=Decimal("0"),
        filled_at="20240102",
        session=EventType.OPEN_MATCH,
    )
    p.apply_fill(buy)
    assert p.cash == Decimal("8995.00")  # 10000 - 1000 - 5 commission
    assert p.positions["600000.SH"].quantity == 100
    assert p.positions["600000.SH"].sellable_quantity == 0  # T+1

    # settle before next day's sell
    p.settle_t1(today="20240102", previous_date=None)
    assert p.positions["600000.SH"].sellable_quantity == 100

    sell = Fill.from_trade(
        fill_id="F002",
        order_id="O002",
        symbol="600000.SH",
        side=Side.SELL,
        quantity=100,
        price=Decimal("12.00"),
        commission=Decimal("5.00"),
        stamp_tax=Decimal("12.00"),  # 12.00 * 0.001 = 0.012 -> placeholder 12.00
        other_fee=Decimal("0"),
        filled_at="20240103",
        session=EventType.OPEN_MATCH,
    )
    p.apply_fill(sell)
    # cash_in = 1200 - 5 - 12 = 1183
    # cash = 8995 + 1183 = 10178
    assert p.cash == Decimal("10178.00")
    assert p.positions["600000.SH"].quantity == 0
    # realized = (12 - 10) * 100 = 200
    assert p.realized_pnl == Decimal("200.00")


def test_total_equity_uses_market_value():
    p = Portfolio(initial_cash=Decimal("5000.00"))
    fill = Fill.from_trade(
        fill_id="F001",
        order_id="O001",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=100,
        price=Decimal("10.00"),
        commission=Decimal("0"),
        stamp_tax=Decimal("0"),
        other_fee=Decimal("0"),
        filled_at="20240102",
        session=EventType.OPEN_MATCH,
    )
    p.apply_fill(fill)
    # 5000 - 1000 = 4000
    assert p.cash == Decimal("4000.00")
    mv = p.market_value({"600000.SH": Decimal("15.00")})
    assert mv == Decimal("1500.00")
    assert p.total_equity({"600000.SH": Decimal("15.00")}) == Decimal("5500.00")


def test_apply_fill_rejects_non_open_match_session():
    p = Portfolio(initial_cash=Decimal("10000.00"))
    fill = Fill.from_trade(
        fill_id="F001",
        order_id="O001",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=100,
        price=Decimal("10.00"),
        commission=Decimal("0"),
        stamp_tax=Decimal("0"),
        other_fee=Decimal("0"),
        filled_at="20240102",
        session=EventType.BAR_CLOSE,  # not allowed in v0.1
    )
    with pytest.raises(ValueError):
        p.apply_fill(fill)


def test_apply_fill_rejects_buy_that_would_make_cash_negative():
    p = Portfolio(initial_cash=Decimal("100.00"))
    fill = Fill.from_trade(
        fill_id="F001",
        order_id="O001",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=100,
        price=Decimal("10.00"),
        commission=Decimal("0"),
        stamp_tax=Decimal("0"),
        other_fee=Decimal("0"),
        filled_at="20240102",
        session=EventType.OPEN_MATCH,
    )
    with pytest.raises(ValueError, match="insufficient cash"):
        p.apply_fill(fill)
    assert p.cash == Decimal("100.00")
    assert p.positions == {}
