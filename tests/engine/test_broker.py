"""Tests for the SimulatedBroker and the engine-side matching flow.

Covers task 7 verification items:
    * buy happy path / sell happy path
    * insufficient cash / insufficient sellable shares
    * missing bar (suspended / non-trading day)
    * invalid open price
    * same-day vs next-day matching (BAR_CLOSE(D) order only matches at D+1)
    * conservation (cash + market_value = initial + sum of net fills)
    * determinism
"""

from decimal import Decimal

import pytest

from hqbacktest.data import DataView, InMemoryDataPortal
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import (
    EventType,
    OrderStatus,
    OrderType,
    RejectReason,
    Side,
)
from hqbacktest.domain.order import Order
from hqbacktest.domain.portfolio import Portfolio
from hqbacktest.domain.position import Position
from hqbacktest.engine.broker import SimulatedBroker
from hqbacktest.engine.config import BacktestConfig
from hqbacktest.engine.context import Context
from hqbacktest.engine.engine import BacktestEngine
from hqbacktest.engine.errors import RunFailed
from hqbacktest.engine.events import EventLog
from hqbacktest.engine.strategy import BaseStrategy


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def _bar(date: str, open_: str = "10.00", close: str = "10.50") -> Bar:
    return Bar.from_raw(
        symbol="600000.SH",
        date=date,
        open=open_,
        high="11.00",
        low="9.00",
        close=close,
        volume=1000,
    )


def _portal(dates: list[str], open_by_date: dict[str, str] | None = None):
    p = InMemoryDataPortal(calendar=dates)
    open_by_date = open_by_date or {}
    for d in dates:
        open_ = open_by_date.get(d, "10.00")
        close = "10.50"
        p.add_bar(
            Bar.from_raw(
                symbol="600000.SH",
                date=d,
                open=open_,
                high="11.00",
                low="9.00",
                close=close,
                volume=1000,
            )
        )
    return p


def _order(
    symbol: str,
    side: Side,
    qty: int,
    *,
    order_id: str = "O-test-1",
    created_at: str = "20240102",
    created_session: EventType = EventType.BAR_CLOSE,
    order_type: OrderType = OrderType.MARKET,
) -> Order:
    return Order(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=qty,
        order_type=order_type,
        created_at=created_at,
        created_session=created_session,
    )


def _ready_order(order: Order) -> Order:
    order.transition(OrderStatus.ACCEPTED, at=order.created_at)
    order.transition(OrderStatus.PENDING, at=order.created_at)
    return order


# --------------------------------------------------------------------- #
# Direct broker.match() tests (no engine)
# --------------------------------------------------------------------- #


def test_broker_match_buy_returns_fill():
    portal = _portal(["20240102"])
    broker = SimulatedBroker()
    order = _ready_order(_order("600000.SH", Side.BUY, 100))
    results = broker.match([order], portal, "20240102")
    order_result, fill, reason, detail = results[0]
    assert reason is None and detail is None
    assert fill is not None
    assert fill.symbol == "600000.SH"
    assert fill.side is Side.BUY
    assert fill.quantity == 100
    assert fill.price == Decimal("10.0000")
    assert fill.amount == Decimal("1000.00")
    assert fill.session is EventType.OPEN_MATCH


def test_broker_match_sell_returns_fill():
    portal = _portal(["20240102"])
    broker = SimulatedBroker()
    order = _ready_order(_order("600000.SH", Side.SELL, 100))
    results = broker.match([order], portal, "20240102")
    _, fill, reason, _ = results[0]
    assert reason is None
    assert fill is not None
    assert fill.side is Side.SELL
    assert fill.amount == Decimal("-1000.00")


def test_broker_match_rejects_missing_bar():
    portal = _portal(["20240102"])  # only one day
    broker = SimulatedBroker()
    order = _ready_order(_order("600000.SH", Side.BUY, 100))
    results = broker.match([order], portal, "20240103")  # not in calendar
    _, fill, reason, detail = results[0]
    assert fill is None
    assert reason is RejectReason.MISSING_DATA


def test_broker_match_rejects_invalid_price():
    portal = _portal(["20240102"])
    # Inject an invalid open price into the stored bar by bypassing the
    # frozen-dataclass invariant (Bar.__post_init__ would reject <=0).
    bar = portal.bars_by_symbol["600000.SH"][0]
    object.__setattr__(bar, "open", None)
    broker = SimulatedBroker()
    order = _ready_order(_order("600000.SH", Side.BUY, 100))
    results = broker.match([order], portal, "20240102")
    _, fill, reason, _ = results[0]
    assert fill is None
    assert reason is RejectReason.INVALID_PRICE


def test_broker_match_skips_non_pending_order():
    portal = _portal(["20240102"])
    broker = SimulatedBroker()
    order = _order("600000.SH", Side.BUY, 100)  # NEW, not PENDING
    results = broker.match([order], portal, "20240102")
    _, fill, reason, _ = results[0]
    assert fill is None
    assert reason is RejectReason.OTHER


def test_broker_match_produces_monotonic_fill_ids():
    portal = _portal(["20240102"])
    broker = SimulatedBroker()
    orders = [
        _ready_order(_order("600000.SH", Side.BUY, 100, order_id="O1")),
        _ready_order(_order("600000.SH", Side.BUY, 100, order_id="O2")),
    ]
    results = broker.match(orders, portal, "20240102")
    fill_ids = [r[1].fill_id for r in results]
    assert fill_ids[0] != fill_ids[1]
    assert fill_ids[0].startswith("F20240102-")
    assert fill_ids[1].startswith("F20240102-")


# --------------------------------------------------------------------- #
# Engine-side matching flow (e2e)
# --------------------------------------------------------------------- #


def _config(start="20240102", end="20240104"):
    return BacktestConfig(
        start_date=start,
        end_date=end,
        initial_cash=Decimal("100000"),
        source="tushare",
    )


def test_engine_buy_and_hold_fills_at_next_open():
    """Orders created at BAR_CLOSE(D) match only at OPEN_MATCH(D+1)."""

    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    # 3 trading days. on_bar fires on 20240102, 20240103, 20240104.
    # Orders match at OPEN_MATCH(D+1) = 20240103, 20240104.
    # The third order (placed on 20240104) has no OPEN_MATCH left in the
    # window: contract §4 requires it to become CANCELLED / BACKTEST_ENDED.
    p = _portal(["20240102", "20240103", "20240104"])
    engine = BacktestEngine(
        _config("20240102", "20240104"),
        strategy=BuyHold(),
        portal=p,
    )
    engine.run()
    fills = [e for e in engine.event_log.all() if e.fill_id is not None]
    # Two fills: orders from 20240102 and 20240103 matched at D+1.
    assert len(fills) == 2
    # First fill: order placed on 20240102, matched at 20240103.
    assert fills[0].date == "20240103"
    assert fills[1].date == "20240104"


def test_engine_cancels_leftover_orders_with_backtest_ended():
    """Contract §4: unfilled orders after the last BAR_CLOSE become CANCELLED
    with reason BACKTEST_ENDED; the engine never extends the run."""

    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            self.last_order = context.order("600000.SH", 100)

    strategy = BuyHold()
    p = _portal(["20240102", "20240103"])
    engine = BacktestEngine(
        _config("20240102", "20240103"), strategy=strategy, portal=p
    )
    engine.run()
    # The order placed on the last day's BAR_CLOSE is cancelled, not filled.
    order = strategy.last_order
    assert order.status is OrderStatus.CANCELLED
    assert order.reject_reason is RejectReason.BACKTEST_ENDED
    cancellations = [
        e for e in engine.event_log.all() if e.phase is EventType.ORDER_CANCELLED
    ]
    assert len(cancellations) == 1
    assert cancellations[0].order_id == order.order_id
    assert cancellations[0].date == "20240103"
    assert "BACKTEST_ENDED" in cancellations[0].detail


def test_engine_fill_links_order_and_fill_ids():
    """Task 7 verification: order/fill/ledger IDs stay associated."""

    class BuyOnce(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20240102":
                self.order = context.order("600000.SH", 100)

    strategy = BuyOnce()
    p = _portal(["20240102", "20240103"])
    engine = BacktestEngine(
        _config("20240102", "20240103"), strategy=strategy, portal=p
    )
    engine.run()
    order = strategy.order
    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == 100
    assert order.avg_fill_price == Decimal("10.0000")
    assert len(order.fill_ids) == 1
    fill_events = [e for e in engine.event_log.all() if e.fill_id is not None]
    assert fill_events[0].fill_id == order.fill_ids[0]
    assert fill_events[0].order_id == order.order_id


def test_engine_corrupt_bar_data_aborts_run_not_rejection():
    """Contract rule 12: infrastructure/data-integrity errors (InvalidDataError)
    must abort the run, not be downgraded to a MISSING_DATA order rejection."""

    class CorruptPortal:
        """Portal whose bar lookup raises InvalidDataError (corrupt snapshot)."""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def get_bars(self, symbol, start, end):
            from hqbacktest.data.errors import InvalidDataError

            raise InvalidDataError("bars", "corrupt snapshot")

    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    inner = _portal(["20240102", "20240103"])
    engine = BacktestEngine(
        _config("20240102", "20240103"),
        strategy=BuyHold(),
        portal=CorruptPortal(inner),
    )
    with pytest.raises(RunFailed):
        engine.run()
    # No order was silently rejected as MISSING_DATA.
    rejections = [e for e in engine.event_log.all() if e.error == "MISSING_DATA"]
    assert rejections == []


def test_engine_conservation_cash_plus_market_value():
    """After every day, cash + market_value(initial_cash) equals initial + sum(net fills)."""

    class Alternate(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])
            self.day_index = 0

        def on_bar(self, context, data):
            self.day_index += 1
            # Alternate: buy then sell
            if self.day_index % 2 == 1:
                context.order("600000.SH", 100)
            else:
                # Sell the 100 we just bought (after settle_t1)
                context.order("600000.SH", -100)

    p = _portal(["20240102", "20240103", "20240104", "20240105"])
    engine = BacktestEngine(
        _config("20240102", "20240105"), strategy=Alternate(), portal=p
    )
    engine.run()
    portfolio = engine.portfolio
    # In this schedule: day 1 BUY (matches day 2), day 2 SELL (matches day 3),
    # day 3 BUY (matches day 4), day 4 SELL (matches day 5 = end_date, no match).
    # End-of-run orders stay PENDING (no OPEN_MATCH after the last day).
    # Cash flow: -1000, +1000, -1000.
    # At end: cash = 99000. Position: 100 shares. Market value: 1000.
    assert portfolio.cash == Decimal("99000.00")
    assert portfolio.market_value({"600000.SH": Decimal("10.0000")}) == Decimal(
        "1000.00"
    )
    assert portfolio.cash + portfolio.market_value(
        {"600000.SH": Decimal("10.0000")}
    ) == Decimal("100000.00")


def test_engine_insufficient_cash_rejects_order():
    """A buy that exceeds cash is rejected with INSUFFICIENT_CASH at apply_fill."""

    class TryToOverSpend(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            # 10000 shares @ 10 = 100000; initial cash = 100000.
            # After first day's BUY, cash = 90000. Second day's BUY of 10000
            # requires 100000 -> rejected.
            context.order("600000.SH", 10000)

    p = _portal(["20240102", "20240103", "20240104"])
    engine = BacktestEngine(_config(), strategy=TryToOverSpend(), portal=p)
    engine.run()
    rejections = [e for e in engine.event_log.all() if e.error == "INSUFFICIENT_CASH"]
    assert len(rejections) >= 1


def test_engine_insufficient_shares_rejects_sell():
    """A sell that exceeds sellable quantity is rejected with INSUFFICIENT_SHARES."""

    class TryToOverSell(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            # Sell 100 but we own 0 (no prior buy).
            context.order("600000.SH", -100)

    p = _portal(["20240102", "20240103"])
    engine = BacktestEngine(
        _config("20240102", "20240103"), strategy=TryToOverSell(), portal=p
    )
    engine.run()
    rejections = [e for e in engine.event_log.all() if e.error == "INSUFFICIENT_SHARES"]
    assert len(rejections) == 1


def test_engine_t_plus_one_sell_only_works_after_settle():
    """Day-1 BUY 100 (settle next day); Day-2 SELL 100 must match."""

    class BuyThenSell(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            # We buy on day 1's BAR_CLOSE; sell on day 2's BAR_CLOSE.
            if context.now == "20240102":
                context.order("600000.SH", 100)
            elif context.now == "20240103":
                context.order("600000.SH", -100)

    p = _portal(["20240102", "20240103", "20240104"])
    engine = BacktestEngine(
        _config("20240102", "20240104"), strategy=BuyThenSell(), portal=p
    )
    engine.run()
    portfolio = engine.portfolio
    assert portfolio.cash == Decimal("100000")  # bought at 10, sold at 10
    # No positions left.
    assert all(pos.quantity == 0 for pos in portfolio.positions.values())


def test_engine_missing_open_price_rejects_order():
    """An order against a date without a bar triggers MISSING_DATA rejection."""

    class SubmitAlways(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    # Two trading days; remove the bar on day 3 so OPEN_MATCH(20240103)
    # finds no bar.
    p = InMemoryDataPortal(calendar=["20240102", "20240103"])
    p.add_bar(_bar("20240102"))
    # No bar on 20240103 -> MISSING_DATA.
    engine = BacktestEngine(
        _config("20240102", "20240103"), strategy=SubmitAlways(), portal=p
    )
    engine.run()
    errors = [e for e in engine.event_log.all() if e.error == "MISSING_DATA"]
    assert len(errors) == 1


def test_engine_invalid_open_price_rejects_order():
    """An open price that the broker can't use (None / <= 0) yields INVALID_PRICE."""

    class SubmitAlways(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    p = InMemoryDataPortal(calendar=["20240102", "20240103"])
    p.add_bar(_bar("20240102"))
    p.add_bar(_bar("20240103"))
    # Inject an invalid open price into the day-3 bar.
    bar = p.bars_by_symbol["600000.SH"][1]
    object.__setattr__(bar, "open", None)
    engine = BacktestEngine(
        _config("20240102", "20240103"), strategy=SubmitAlways(), portal=p
    )
    engine.run()
    errors = [e for e in engine.event_log.all() if e.error == "INVALID_PRICE"]
    assert len(errors) == 1


def test_engine_deterministic_same_input_same_output():
    """Two runs of the same config + portal + strategy produce identical event logs."""

    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    p1 = _portal(["20240102", "20240103", "20240104"])
    p2 = _portal(["20240102", "20240103", "20240104"])
    a = BacktestEngine(_config(), strategy=BuyHold(), portal=p1).run()
    b = BacktestEngine(_config(), strategy=BuyHold(), portal=p2).run()
    log_a = [e.to_dict() for e in a.event_log.all()]
    log_b = [e.to_dict() for e in b.event_log.all()]
    assert log_a == log_b


def test_engine_settle_t1_rolls_pending_buy_into_sellable():
    """Day 1 BUY's `pending_today_buy` becomes sellable on day 2."""

    class BuyAndSell(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])
            self.bought_day = None

        def on_bar(self, context, data):
            if self.bought_day is None:
                context.order("600000.SH", 100)
                self.bought_day = context.now
            elif context.now != self.bought_day:
                # Try to sell 100 (after settle_t1).
                context.order("600000.SH", -100)

    p = _portal(["20240102", "20240103", "20240104"])
    engine = BacktestEngine(
        _config("20240102", "20240104"), strategy=BuyAndSell(), portal=p
    )
    engine.run()
    rejections = [e for e in engine.event_log.all() if e.error == "INSUFFICIENT_SHARES"]
    # No rejections: settled T+1 lets us sell on day 2.
    assert rejections == []


def test_engine_conservation_holds_at_every_day_end():
    """Task 7 verification: cash + Σ(quantity × avg_cost) == initial cash +
    realized pnl at the end of EVERY trading day, not just at run end.

    Uniform prices (open == close == 10.00) and zero fees keep the identity
    exact: realized pnl is always 0 and avg_cost is exactly 10.0000.
    """

    class Alternating(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])
            self.checked_days = 0

        def on_bar(self, context, data):
            # Alternate 100-share buys and sells.
            if int(context.now[-1]) % 2 == 0:
                context.order("600000.SH", 100)
            else:
                context.order("600000.SH", -100)

        def after_trading_end(self, context):
            positions = context.positions()
            held_value = sum(
                Decimal(pos.quantity) * pos.avg_cost for pos in positions.values()
            )
            assert context.cash() + held_value == Decimal("100000")
            self.checked_days += 1

    # All bars at exactly 10.00 so the identity has no rounding drift.
    days = ["20240102", "20240103", "20240104", "20240105"]
    p = InMemoryDataPortal(calendar=days)
    for d in days:
        p.add_bar(
            Bar.from_raw(
                symbol="600000.SH",
                date=d,
                open="10.00",
                high="10.00",
                low="10.00",
                close="10.00",
                volume=1000,
            )
        )
    strategy = Alternating()
    engine = BacktestEngine(
        _config("20240102", "20240105"), strategy=strategy, portal=p
    )
    engine.run()
    # The invariant was asserted inside after_trading_end for all 4 days.
    assert strategy.checked_days == 4
