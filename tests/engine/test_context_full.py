"""Tests for the full Context API (read-only accessors + order intents + lifecycle).

These tests use a tiny three-day `InMemoryDataPortal` snapshot and exercise
Context directly, without a `BacktestEngine`. The goal is to lock in the
contract for task 6: read-only access, no ledger mutation, lot alignment,
order-type allow-list, lifecycle errors.
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from hqbacktest.data import DataView, InMemoryDataPortal
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import (
    EventType,
    OrderStatus,
    OrderType,
    Side,
)
from hqbacktest.domain.portfolio import Portfolio
from hqbacktest.engine.context import Context
from hqbacktest.engine.errors import (
    CallbackAfterRunError,
    DoubleInitializationError,
    NoPriceForOrderError,
    NotInitializedError,
    StrategyLifecycleError,
    UnsupportedOrderTypeError,
)
from hqbacktest.engine.events import EngineEvent, EventLog


def _bar(date: str, close: str) -> Bar:
    return Bar.from_raw(
        symbol="600000.SH",
        date=date,
        open="10.00",
        high="11.00",
        low="9.00",
        close=close,
        volume=1000,
    )


def _portal():
    p = InMemoryDataPortal(calendar=["20240102", "20240103"])
    p.add_bar(_bar("20240102", "10.00"))
    p.add_bar(_bar("20240103", "10.50"))
    return p


def _ctx(portal=None, *, current_date="20240103", phase=EventType.BAR_CLOSE):
    portal = portal or _portal()
    portfolio = Portfolio(initial_cash=Decimal("100000"))
    log = EventLog()
    ctx = Context(
        current_date=current_date,
        portfolio=portfolio,
        event_log=log,
        data_view=DataView(portal=portal, visible_through=current_date),
    )
    ctx._mark_initialized()
    ctx._set_phase(phase)
    return ctx, portfolio, log


# --------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------- #


def test_access_before_initialize_raises_not_initialized():
    portfolio = Portfolio(initial_cash=Decimal("0"))
    log = EventLog()
    ctx = Context(
        current_date="20240102",
        portfolio=portfolio,
        event_log=log,
        data_view=DataView(portal=_portal(), visible_through="20240102"),
    )
    with pytest.raises(NotInitializedError):
        ctx.cash()


def test_double_initialize_raises_double_initialization():
    ctx, _, _ = _ctx()
    with pytest.raises(DoubleInitializationError):
        ctx._mark_initialized()


def test_access_after_run_finished_raises_callback_after_run():
    ctx, _, _ = _ctx()
    ctx._mark_run_finished()
    with pytest.raises(CallbackAfterRunError):
        ctx.cash()


def test_set_universe_validates_symbols():
    ctx, _, _ = _ctx()
    ctx.set_universe(["600000.SH", "000001.SZ"])
    assert ctx.universe() == ["600000.SH", "000001.SZ"]


def test_set_universe_rejects_invalid_symbol():
    ctx, _, _ = _ctx()
    with pytest.raises(Exception):
        ctx.set_universe(["not-a-symbol"])


def test_set_universe_dedupes_and_preserves_order():
    ctx, _, _ = _ctx()
    ctx.set_universe(["600000.SH", "000001.SZ", "600000.SH"])
    assert ctx.universe() == ["600000.SH", "000001.SZ"]


def test_set_universe_accepts_single_string():
    ctx, _, _ = _ctx()
    ctx.set_universe("600000.SH")
    assert ctx.universe() == ["600000.SH"]


# --------------------------------------------------------------------- #
# Read-only accessors
# --------------------------------------------------------------------- #


def test_cash_returns_initial_cash():
    ctx, portfolio, _ = _ctx()
    assert ctx.cash() == Decimal("100000")


def test_positions_starts_empty():
    ctx, _, _ = _ctx()
    assert ctx.positions() == {}


def test_universe_starts_empty():
    ctx, _, _ = _ctx()
    assert ctx.universe() == []


def test_total_equity_returns_cash_when_no_positions():
    ctx, _, _ = _ctx()
    assert ctx.total_equity() == Decimal("100000")


def test_history_returns_visible_bars():
    ctx, _, _ = _ctx()
    closes = ctx.history("600000.SH", field="close", bar_count=2)
    assert [str(c) for c in closes] == ["10.0000", "10.5000"]


def test_current_price_returns_latest_visible_close():
    ctx, _, _ = _ctx()
    assert ctx.current_price("600000.SH") == Decimal("10.5000")


def test_current_price_returns_none_for_unknown_symbol():
    ctx, _, _ = _ctx()
    assert ctx.current_price("688001.SH") is None


def test_pending_orders_starts_empty():
    ctx, _, _ = _ctx()
    assert ctx.pending_orders() == []


# --------------------------------------------------------------------- #
# Direct order()
# --------------------------------------------------------------------- #


def test_order_buy_creates_pending_order():
    ctx, _, _ = _ctx()
    order = ctx.order("600000.SH", 200)
    assert order.symbol == "600000.SH"
    assert order.side is Side.BUY
    assert order.quantity == 200
    assert order.status is OrderStatus.PENDING
    assert ctx.pending_orders() == [order]


def test_order_sell_uses_negative_quantity():
    ctx, _, _ = _ctx()
    order = ctx.order("600000.SH", -300)
    assert order.side is Side.SELL
    assert order.quantity == 300


def test_order_zero_quantity_is_noop():
    ctx, _, _ = _ctx()
    assert ctx.order("600000.SH", 0) is None
    assert ctx.pending_orders() == []


def test_order_rounds_down_to_lot():
    ctx, _, _ = _ctx()
    order = ctx.order("600000.SH", 250)
    assert order.quantity == 200  # 250 -> 2 lots


def test_order_rejects_below_one_lot():
    ctx, _, _ = _ctx()
    with pytest.raises(StrategyLifecycleError):
        ctx.order("600000.SH", 50)


def test_order_rejects_non_int_quantity():
    ctx, _, _ = _ctx()
    with pytest.raises(StrategyLifecycleError):
        ctx.order("600000.SH", 100.0)  # type: ignore[arg-type]


def test_order_rejects_unknown_type():
    ctx, _, _ = _ctx()
    with pytest.raises(UnsupportedOrderTypeError):
        ctx.order("600000.SH", 100, order_type="LIMIT")  # type: ignore[arg-type]


def test_order_rejects_non_enum_type():
    ctx, _, _ = _ctx()
    with pytest.raises(UnsupportedOrderTypeError):
        ctx.order("600000.SH", 100, order_type="MARKET_NOT_REAL")


def test_order_rejects_market_when_only_other_supported():
    """Contract rule 7: any non-MARKET type must be rejected."""
    # Since OrderType only has MARKET in v0.1, we verify the only enum
    # value is accepted.
    ctx, _, _ = _ctx()
    order = ctx.order("600000.SH", 100, order_type=OrderType.MARKET)
    assert order.order_type is OrderType.MARKET


# --------------------------------------------------------------------- #
# order_value / order_target / order_target_value / order_target_percent
# --------------------------------------------------------------------- #


def test_order_value_buy():
    ctx, _, _ = _ctx()
    # 12.5 CNY / share * 100 shares = 1250 CNY -> 100 shares.
    order = ctx.order_value("600000.SH", Decimal("1250"))
    assert order.side is Side.BUY
    assert order.quantity == 100


def test_order_value_sell():
    ctx, _, _ = _ctx()
    # SELL 2500 CNY worth -> 200 shares (lot aligned).
    order = ctx.order_value("600000.SH", Decimal("-2500"))
    assert order.side is Side.SELL
    assert order.quantity == 200


def test_order_value_zero_is_noop():
    ctx, _, _ = _ctx()
    assert ctx.order_value("600000.SH", Decimal("0")) is None
    assert ctx.pending_orders() == []


def test_order_value_rejects_when_no_price():
    ctx, _, _ = _ctx()
    with pytest.raises(NoPriceForOrderError):
        ctx.order_value("688001.SH", Decimal("1000"))


def test_order_target_increases_position():
    ctx, portfolio, _ = _ctx()
    # Pre-load a position of 100 shares via direct ledger mutation (test-only).
    portfolio.get_position("600000.SH").update_buy(100, Decimal("10"))
    order = ctx.order_target("600000.SH", 500)
    assert order.side is Side.BUY
    # Diff = 400 -> 4 lots.
    assert order.quantity == 400


def test_order_target_decreases_position():
    ctx, portfolio, _ = _ctx()
    portfolio.get_position("600000.SH").update_buy(500, Decimal("10"))
    portfolio.positions["600000.SH"].settle_t1()
    order = ctx.order_target("600000.SH", 200)
    assert order.side is Side.SELL
    # Diff = -300 -> 3 lots SELL.
    assert order.quantity == 300


def test_order_target_flat_is_noop():
    ctx, portfolio, _ = _ctx()
    portfolio.get_position("600000.SH").update_buy(200, Decimal("10"))
    assert ctx.order_target("600000.SH", 200) is None


def test_order_target_from_zero():
    ctx, _, _ = _ctx()
    order = ctx.order_target("600000.SH", 300)
    assert order.side is Side.BUY
    assert order.quantity == 300


def test_order_target_rejects_negative():
    ctx, _, _ = _ctx()
    with pytest.raises(StrategyLifecycleError):
        ctx.order_target("600000.SH", -100)


def test_order_target_value_recomputes_diff():
    ctx, portfolio, _ = _ctx()
    portfolio.get_position("600000.SH").update_buy(100, Decimal("10"))
    # Current value = 100 * 10.50 = 1050 CNY. Target = 5000 CNY. Diff = 3950.
    # Quantity = 3950 / 10.50 = 376.19 -> 300 shares (3 lots).
    order = ctx.order_target_value("600000.SH", Decimal("5000"))
    assert order.side is Side.BUY
    assert order.quantity == 300


def test_order_target_value_rejects_no_price():
    ctx, _, _ = _ctx()
    with pytest.raises(NoPriceForOrderError):
        ctx.order_target_value("688001.SH", Decimal("1000"))


def test_order_target_percent_allocates_share_of_equity():
    ctx, _, _ = _ctx()
    order = ctx.order_target_percent("600000.SH", Decimal("0.5"))
    # 50% of 100000 = 50000. Quantity = 50000 / 10.50 = 4761 -> 4700 shares (47 lots).
    assert order.side is Side.BUY
    assert order.quantity == 4700


def test_order_target_percent_zero_flattens_with_sell():
    ctx, portfolio, _ = _ctx()
    portfolio.get_position("600000.SH").update_buy(200, Decimal("10"))
    order = ctx.order_target_percent("600000.SH", Decimal("0"))
    assert order is not None
    assert order.side is Side.SELL
    assert order.quantity == 200


def test_order_target_percent_one_buys_max():
    ctx, _, _ = _ctx()
    order = ctx.order_target_percent("600000.SH", Decimal("1"))
    assert order.side is Side.BUY


def test_order_target_percent_rejects_out_of_range():
    ctx, _, _ = _ctx()
    with pytest.raises(StrategyLifecycleError):
        ctx.order_target_percent("600000.SH", Decimal("1.1"))
    with pytest.raises(StrategyLifecycleError):
        ctx.order_target_percent("600000.SH", Decimal("-0.1"))


def test_order_target_percent_rejects_float():
    ctx, _, _ = _ctx()
    with pytest.raises(StrategyLifecycleError):
        ctx.order_target_percent("600000.SH", 0.5)


# --------------------------------------------------------------------- #
# cancel_order
# --------------------------------------------------------------------- #


def test_cancel_order_returns_true_for_existing_order():
    ctx, _, _ = _ctx()
    order = ctx.order("600000.SH", 100)
    assert ctx.cancel_order(order.order_id) is True
    assert ctx.pending_orders() == []


def test_cancel_order_returns_false_for_missing_order():
    ctx, _, _ = _ctx()
    assert ctx.cancel_order("missing-id") is False


# --------------------------------------------------------------------- #
# Isolation: context never mutates the ledger
# --------------------------------------------------------------------- #


def test_context_does_not_modify_portfolio_cash():
    ctx, portfolio, _ = _ctx()
    initial_cash = portfolio.cash
    ctx.order("600000.SH", 100)
    ctx.order_target("600000.SH", 500)
    ctx.order_value("600000.SH", Decimal("1000"))
    assert portfolio.cash == initial_cash


def test_context_does_not_modify_positions_outside_pending_orders():
    ctx, portfolio, _ = _ctx()
    ctx.order("600000.SH", 100)
    assert (
        "600000.SH" not in portfolio.positions
        or portfolio.positions["600000.SH"].quantity == 0
    )


def test_context_does_not_expose_raw_portal_or_portfolio():
    ctx, _, _ = _ctx()
    public_attrs = {name for name in dir(ctx) if not name.startswith("_")}
    # No public attribute leaks the underlying portal or the portfolio.
    assert "portal" not in public_attrs
    assert "portfolio" not in public_attrs
    # The public surface is the accessor methods + read-only views.
    assert "cash" in public_attrs
    assert "positions" in public_attrs
    assert "universe" in public_attrs


def test_context_positions_are_copies_not_live_ledger_objects():
    """Isolation: mutating a returned Position must not rewrite the ledger."""
    ctx, portfolio, _ = _ctx()
    portfolio.get_position("600000.SH").update_buy(100, Decimal("10"))
    leaked = ctx.position("600000.SH")
    leaked.update_buy(10000, Decimal("1"))  # strategy-side forgery attempt
    assert portfolio.positions["600000.SH"].quantity == 100
    snapshot = ctx.positions()["600000.SH"]
    snapshot.quantity = 999999
    assert portfolio.positions["600000.SH"].quantity == 100


# --------------------------------------------------------------------- #
# Order event recording
# --------------------------------------------------------------------- #


def test_pending_order_attaches_created_session():
    ctx, _, log = _ctx(phase=EventType.BEFORE_TRADING_START)
    order = ctx.order("600000.SH", 100)
    assert order.created_session is EventType.BEFORE_TRADING_START


def test_pending_order_at_bar_close_uses_bar_close_session():
    ctx, _, log = _ctx(phase=EventType.BAR_CLOSE)
    order = ctx.order("600000.SH", 100)
    assert order.created_session is EventType.BAR_CLOSE


# --------------------------------------------------------------------- #
# Phase gating (contract §4: orders and data only in the right phases)
# --------------------------------------------------------------------- #


def test_order_rejected_outside_orderable_phases():
    for phase in (
        EventType.SESSION_START,
        EventType.OPEN_MATCH,
        EventType.AFTER_TRADING_END,
        None,
    ):
        ctx, _, _ = _ctx(phase=phase)
        with pytest.raises(StrategyLifecycleError):
            ctx.order("600000.SH", 100)


def test_cancel_rejected_outside_orderable_phases():
    ctx, _, _ = _ctx(phase=EventType.AFTER_TRADING_END)
    with pytest.raises(StrategyLifecycleError):
        ctx.cancel_order("any-id")


def test_data_unreadable_without_published_view():
    """initialize / SESSION_START have no market data access (contract §4)."""
    portfolio = Portfolio(initial_cash=Decimal("100000"))
    ctx = Context(
        current_date="20240103",
        portfolio=portfolio,
        event_log=EventLog(),
    )
    ctx._mark_initialized()
    ctx._set_phase(EventType.SESSION_START)
    with pytest.raises(StrategyLifecycleError):
        ctx.history("600000.SH")
    with pytest.raises(StrategyLifecycleError):
        ctx.current_price("600000.SH")
    assert ctx.visible_through == ""


def test_set_universe_locked_after_initialize():
    ctx, _, _ = _ctx()
    ctx._lock_universe()  # engine does this once initialize() returns
    with pytest.raises(StrategyLifecycleError):
        ctx.set_universe(["600000.SH"])


def test_order_type_allow_list_cannot_be_bypassed_via_helpers():
    """Contract rule 7: every order path must reject non-MARKET types."""
    ctx, _, _ = _ctx()
    with pytest.raises(UnsupportedOrderTypeError):
        ctx.order_value("600000.SH", Decimal("1250"), order_type="LIMIT")  # type: ignore[arg-type]
    with pytest.raises(UnsupportedOrderTypeError):
        ctx.order_target("600000.SH", 500, order_type="LIMIT")  # type: ignore[arg-type]
    with pytest.raises(UnsupportedOrderTypeError):
        ctx.order_target_percent("600000.SH", Decimal("0.5"), order_type="LIMIT")  # type: ignore[arg-type]
    assert ctx.pending_orders() == []


def test_order_rejects_malformed_symbol():
    ctx, _, _ = _ctx()
    with pytest.raises(Exception):
        ctx.order("not-a-symbol", 100)


# --------------------------------------------------------------------- #
# Order lifecycle events in the audit trail
# --------------------------------------------------------------------- #


def test_order_creation_records_order_created_event():
    ctx, _, log = _ctx(phase=EventType.BAR_CLOSE)
    order = ctx.order("600000.SH", 100)
    created = [e for e in log.all() if e.phase is EventType.ORDER_CREATED]
    assert len(created) == 1
    assert created[0].order_id == order.order_id
    assert created[0].date == "20240103"


def test_cancel_records_order_cancelled_event():
    ctx, _, log = _ctx(phase=EventType.BAR_CLOSE)
    order = ctx.order("600000.SH", 100)
    assert ctx.cancel_order(order.order_id) is True
    cancelled = [e for e in log.all() if e.phase is EventType.ORDER_CANCELLED]
    assert len(cancelled) == 1
    assert cancelled[0].order_id == order.order_id
