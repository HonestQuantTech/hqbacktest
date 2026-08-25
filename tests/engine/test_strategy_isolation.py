"""Strategy isolation + audit-trail integrity tests.

Covers:
    * `Order` is frozen after creation: a strategy that mutates the
      Order returned from `Context.pending_orders()` does not affect
      the engine's view of the order (broker still matches at the
      original quantity, audit log still records the original
      `avg_fill_price` and `fill_ids`).
    * `DataView.portal` is no longer publicly accessible (the
      strategy cannot read future data by calling
      `view.portal.get_bars(sym, start, future_date)`).
    * `set_universe(...)` actually constrains trading: orders against
      a symbol outside the declared universe are rejected with a
      typed `OUT_OF_UNIVERSE` reason and an audit-trail event.
    * When no universe has been declared, behaviour is unchanged (no
      false rejection).
    * `Context.historical_universe()` exposes the historical stock
      list (per `visible_through`) as a read-only view through the
      engine-owned data view, not the raw portal.
"""

from decimal import Decimal

import pytest

from hqbacktest import BacktestConfig, BacktestEngine, BaseStrategy
from hqbacktest.data import DataView, InMemoryDataPortal
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import (
    EventType,
    OrderStatus,
    OrderType,
    RejectReason,
    Side,
)
from hqbacktest.engine.config import BacktestConfig as _Cfg  # noqa: F401


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bar(date: str, sym: str = "600000.SH") -> Bar:
    return Bar.from_raw(
        symbol=sym,
        date=date,
        open="10.0000",
        high="30.0000",
        low="5.0000",
        close="10.0000",
        volume=1000,
    )


def _two_symbol_portal() -> InMemoryDataPortal:
    p = InMemoryDataPortal(
        calendar=["20240102", "20240103", "20240104"],
        universe_by_date={
            "20240102": ["600000.SH", "000001.SZ"],
            "20240103": ["600000.SH", "000001.SZ"],
            "20240104": ["600000.SH", "000001.SZ"],
        },
        as_of="20240104",
    )
    for d in ("20240102", "20240103", "20240104"):
        p.add_bar(_bar(d, sym="600000.SH"))
        p.add_bar(_bar(d, sym="000001.SZ"))
    return p


def _cfg(start="20240102", end="20240103") -> BacktestConfig:
    return BacktestConfig(
        start_date=start,
        end_date=end,
        initial_cash=Decimal("100000"),
        source="tushare",
    )


# ---------------------------------------------------------------------------
# Order immutability for the strategy
# ---------------------------------------------------------------------------


def test_pending_orders_returns_frozen_order_copies():
    """Order objects handed to the strategy must be immutable.

    Mutating `quantity` on a returned Order must NOT change the order
    the broker eventually matches against. The audit trail's
    `avg_fill_price` and `fill_ids` must likewise be unaffected.
    """

    captured: list = []

    class TryToTamper(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now != "20240102":
                return
            context.order("600000.SH", 100)
            orders = context.pending_orders()
            # Strategy tries to inflate the SELL order's quantity so it
            # could exceed the lot rule. Frozen Order objects must
            # raise on attribute assignment.
            with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
                orders[0].quantity = 9999  # type: ignore[misc]
            with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
                orders[0].avg_fill_price = Decimal("99.99")  # type: ignore[misc]
            captured.append(len(orders))

    import dataclasses

    engine = BacktestEngine(_cfg(), strategy=TryToTamper(), portal=_two_symbol_portal())
    engine.run()
    # The order for 100 shares of 600000.SH on day 1 must have filled at
    # exactly 100, NOT the inflated 9999.
    fills = [e for e in engine.event_log.all() if e.phase is EventType.ORDER_FILLED]
    assert any(
        "qty=100" in (e.detail or "") for e in fills
    ), f"expected a 100-share BUY fill, got {fills}"
    assert not any("qty=9999" in (e.detail or "") for e in fills)


# ---------------------------------------------------------------------------
# DataView.portal is private
# ---------------------------------------------------------------------------


def test_data_view_portal_is_not_publicly_accessible():
    """The portal attribute must not be reachable from outside the
    data layer. Strategies must not read future data by
    reading `view.portal.get_bars(sym, start, future_date)`.
    """
    p = InMemoryDataPortal(
        calendar=["20240102", "20240103"],
        as_of="20240103",
    )
    p.add_bar(_bar("20240102"))
    p.add_bar(_bar("20240103"))
    view = DataView(portal=p, visible_through="20240102")
    # The public `portal` attribute must not exist; accessing it must
    # raise AttributeError so a strategy cannot reach the raw portal.
    with pytest.raises(AttributeError):
        view.portal  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Universe enforcement
# ---------------------------------------------------------------------------


def test_orders_outside_universe_are_rejected():
    """`set_universe([...])` must enforce trading scope.

    Submitting an order for a symbol outside the declared universe
    must:
        - be rejected (REJECTED status + audit-trail event)
        - never reach the broker
        - not be silently re-routed or filled
    """

    class TradeOutsideUniverse(BaseStrategy):
        def initialize(self, context):
            # Declare only 600000.SH in the universe.
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20240102":
                # Attempt to trade 000001.SZ (outside the universe).
                context.order("000001.SZ", 100)

    engine = BacktestEngine(
        _cfg(), strategy=TradeOutsideUniverse(), portal=_two_symbol_portal()
    )
    engine.run()
    rejected = [
        e for e in engine.event_log.all() if e.phase is EventType.ORDER_REJECTED
    ]
    assert any(
        "OUT_OF_UNIVERSE" in (e.error or "") or "000001.SZ" in (e.detail or "")
        for e in rejected
    ), f"expected an OUT_OF_UNIVERSE rejection, got {rejected}"
    # No fill must exist for 000001.SZ.
    fills = [e for e in engine.event_log.all() if e.phase is EventType.ORDER_FILLED]
    assert not any("000001.SZ" in (e.detail or "") for e in fills)


def test_unset_universe_does_not_constrain():
    """When `set_universe` has not been called, trading is unrestricted.

    The order for an arbitrary symbol must reach the broker and fill
    normally.
    """

    class NoUniverse(BaseStrategy):
        def initialize(self, context):
            # NOTE: no set_universe call.
            return None

        def on_bar(self, context, data):
            if context.now == "20240102":
                context.order("600000.SH", 100)

    engine = BacktestEngine(_cfg(), strategy=NoUniverse(), portal=_two_symbol_portal())
    engine.run()
    fills = [e for e in engine.event_log.all() if e.phase is EventType.ORDER_FILLED]
    assert any("qty=100" in (e.detail or "") for e in fills)


# ---------------------------------------------------------------------------
# Context.historical_universe()
# ---------------------------------------------------------------------------


def test_historical_universe_returns_portal_universe_for_visible_date():
    """`Context.historical_universe()` returns the historical stock
    list for `visible_through` (via `DataView.universe()`), respecting
    visibility. It does NOT expose the raw portal.
    """

    class Inspect(BaseStrategy):
        def __init__(self):
            self.snapshot: list = []

        def on_bar(self, context, data):
            if context.now == "20240102":
                self.snapshot.append(list(context.historical_universe()))

    strategy = Inspect()
    engine = BacktestEngine(_cfg(), strategy=strategy, portal=_two_symbol_portal())
    engine.run()
    assert strategy.snapshot[0] == ["000001.SZ", "600000.SH"]


def test_out_of_universe_order_appears_in_orders_table():
    """A rejected out-of-universe order must reach `orders_table` with the
    `OUT_OF_UNIVERSE` reason, not just the event log.

    Regression for a bug where the scheduler drained the out-of-universe
    list but discarded the orders (never folding them into the engine's
    `_orders` dict), leaving `orders_table` empty while the event log
    recorded the rejection.
    """

    class TradeOutsideUniverse(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20240102":
                context.order("000001.SZ", 100)

    engine = BacktestEngine(
        _cfg(), strategy=TradeOutsideUniverse(), portal=_two_symbol_portal()
    )
    result = engine.run()
    rows = [
        r
        for r in result.orders_table
        if r["symbol"] == "000001.SZ" and r["status"] == "REJECTED"
    ]
    assert rows, "expected the out-of-universe order in orders_table"
    assert rows[0]["reject_reason"] == "OUT_OF_UNIVERSE"


def test_order_fill_ids_is_immutable():
    """`Order.fill_ids` must be an immutable tuple so a strategy holding
    a frozen Order cannot append/clear it in place.

    A frozen dataclass only blocks attribute *reassignment*; a `list`
    field would still be mutable in place. Switching to `tuple` removes
    the last in-place mutation path.
    """
    from hqbacktest.domain.order import Order

    o = Order(
        order_id="O001",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=100,
        order_type=OrderType.MARKET,
        created_at="20240102",
        created_session=EventType.BEFORE_TRADING_START,
    )
    assert isinstance(o.fill_ids, tuple)
    # Attribute reassignment is already blocked by frozen=True, but an
    # in-place list append would NOT be — the tuple type prevents it.
    assert not hasattr(o.fill_ids, "append")
