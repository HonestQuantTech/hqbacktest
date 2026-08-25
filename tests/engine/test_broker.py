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


def _match_one_broker(broker, order, portal, today):
    """Match a single order through the broker with no rule/cash checks."""
    from hqbacktest.engine.rule_set import DEFAULT_V01_RULES, TradingRuleSet

    return broker.match(
        [order],
        portal,
        today,
        TradingRuleSet(DEFAULT_V01_RULES),
        Decimal("1000000"),  # plenty of cash
        lambda _symbol: 10000,  # plenty of sellable
    )[0]


def test_broker_match_buy_returns_fill():
    portal = _portal(["20240102"])
    broker = SimulatedBroker()
    order = _ready_order(_order("600000.SH", Side.BUY, 100))
    _, fill, reason, detail = _match_one_broker(broker, order, portal, "20240102")
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
    _, fill, reason, _ = _match_one_broker(broker, order, portal, "20240102")
    assert reason is None
    assert fill is not None
    assert fill.side is Side.SELL
    assert fill.amount == Decimal("-1000.00")


def test_broker_match_rejects_missing_bar_via_rule_set():
    """A suspended / missing bar is a BUSINESS rejection, not a run failure.

    Contract §4: 停牌标的不可成交, but the run must continue. The broker
    downgrades `MissingDataError` to `bar_available=False` and the
    NonTradingDayRule rejects with MISSING_DATA. Only `InvalidDataError`
    (corrupt snapshot) and I/O errors abort the run.
    """
    portal = _portal(["20240102"])  # only one day
    broker = SimulatedBroker()
    order = _ready_order(_order("600000.SH", Side.BUY, 100))
    _, fill, reason, detail = _match_one_broker(broker, order, portal, "20240103")
    assert fill is None
    assert reason is RejectReason.MISSING_DATA
    assert "non_trading_day" in detail


def test_broker_rejects_priceless_order_even_without_invalid_price_rule():
    """A custom rule set that drops InvalidPriceRule still cannot fill at an
    unknown price: the broker rejects defensively instead of crashing."""
    from hqbacktest.engine.rule_set import (
        LongOnlyRule,
        NonTradingDayRule,
        TradingRuleSet,
    )

    portal = _portal(["20240102"])
    bar = portal.bars_by_symbol["600000.SH"][0]
    object.__setattr__(bar, "open", None)  # invalid price, bar present
    broker = SimulatedBroker()
    order = _ready_order(_order("600000.SH", Side.BUY, 100))
    rules = TradingRuleSet([LongOnlyRule(), NonTradingDayRule()])
    _, fill, reason, _ = broker.match(
        [order],
        portal,
        "20240102",
        rules,
        Decimal("1000000"),
        lambda _symbol: 10000,
    )[0]
    assert fill is None
    assert reason is RejectReason.INVALID_PRICE


def test_broker_match_rejects_invalid_price():
    portal = _portal(["20240102"])
    bar = portal.bars_by_symbol["600000.SH"][0]
    object.__setattr__(bar, "open", None)
    broker = SimulatedBroker()
    order = _ready_order(_order("600000.SH", Side.BUY, 100))
    _, fill, reason, _ = _match_one_broker(broker, order, portal, "20240102")
    assert fill is None
    assert reason is RejectReason.INVALID_PRICE


def test_broker_match_skips_non_pending_order():
    portal = _portal(["20240102"])
    broker = SimulatedBroker()
    order = _order("600000.SH", Side.BUY, 100)  # NEW, not PENDING
    _, fill, reason, _ = _match_one_broker(broker, order, portal, "20240102")
    assert fill is None
    assert reason is RejectReason.OTHER


def test_broker_match_produces_monotonic_fill_ids():
    portal = _portal(["20240102"])
    broker = SimulatedBroker()
    orders = [
        _ready_order(_order("600000.SH", Side.BUY, 100, order_id="O1")),
        _ready_order(_order("600000.SH", Side.BUY, 100, order_id="O2")),
    ]
    results = broker.match(
        orders,
        portal,
        "20240102",
        __import__(
            "hqbacktest.engine.rule_set",
            fromlist=["TradingRuleSet", "DEFAULT_V01_RULES"],
        ).TradingRuleSet(
            __import__(
                "hqbacktest.engine.rule_set", fromlist=["DEFAULT_V01_RULES"]
            ).DEFAULT_V01_RULES
        ),
        Decimal("1000000"),
        lambda _symbol: 10000,
    )
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
    assert cancellations[0].error == "BACKTEST_ENDED"


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
    """Cash + market_value == initial - fees + realized PnL after each day.

    With the default cost model (0.025% commission with 5 CNY floor + 0.1%
    stamp tax on SELL), a buy-sell-buy schedule on a 10 CNY symbol leaves
    the portfolio with a known cash position. The test verifies the cash
    delta equals -sum(commissions) - sum(stamp_taxes), i.e. the v0.1 fees
    are correctly applied and the run still respects cash conservation.
    """

    class Alternate(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])
            self.day_index = 0

        def on_bar(self, context, data):
            self.day_index += 1
            if self.day_index % 2 == 1:
                context.order("600000.SH", 100)
            else:
                context.order("600000.SH", -100)

    p = _portal(["20240102", "20240103", "20240104", "20240105"])
    engine = BacktestEngine(
        _config("20240102", "20240105"), strategy=Alternate(), portal=p
    )
    engine.run()
    portfolio = engine.portfolio
    # Two matched BUYs, one matched SELL (the last SELL is PENDING — no OPEN_MATCH after).
    # Per-fill fees:
    #   BUY 1:  commission = max(1000 * 0.00025, 5) = 5.00
    #   SELL 1: commission = max(5, 5) = 5.00; stamp_tax = 1000 * 0.001 = 1.00
    #   BUY 2:  commission = 5.00
    # Total fees = 5 + 5 + 1 + 5 = 16. Net cash flow = -5 + 994 - 5 = 984 ... wait:
    # Actually gross BUY = -1000, fee = -5; gross SELL = +1000, fee = -5, stamp = -1.
    # Net cash flow = -1005 + 994 - 1005 = -1016.
    # Final cash = 100000 - 1016 = 98984.
    assert portfolio.cash == Decimal("98984.00")
    assert portfolio.market_value({"600000.SH": Decimal("10.0000")}) == Decimal(
        "1000.00"
    )
    assert portfolio.cash + portfolio.market_value(
        {"600000.SH": Decimal("10.0000")}
    ) == Decimal("99984.00")
    # Fees do NOT live in `realized_pnl`: per contract §3.1 / rule 8,
    # realized_pnl is the gross (sell_price - avg_cost) * quantity and
    # stays independent of commission / stamp_tax / other_fee. Fees flow
    # through `cash` only.
    fills = [e for e in engine.event_log.all() if e.phase is EventType.ORDER_FILLED]
    assert len(fills) == 3


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
    # BUY 5 + SELL 5 + SELL stamp 1 = 11. Final cash = 100000 - 11 = 99989.
    assert portfolio.cash == Decimal("99989.00")
    # No positions left.
    assert all(pos.quantity == 0 for pos in portfolio.positions.values())


def test_engine_missing_open_price_rejects_order():
    """An open price <= 0 produces INVALID_PRICE rejection (rule set)."""

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


def test_engine_missing_bar_rejects_order_and_run_continues():
    """An order against a suspended symbol's missing bar is rejected with
    MISSING_DATA; the run completes normally (contract §4: 停牌不可成交)."""

    class SubmitAlways(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    p = InMemoryDataPortal(calendar=["20240102", "20240103"])
    p.add_bar(_bar("20240102"))
    # No bar on 20240103 -> order rejected, run completes.
    engine = BacktestEngine(
        _config("20240102", "20240103"), strategy=SubmitAlways(), portal=p
    )
    result = engine.run()
    assert result.trading_days == ["20240102", "20240103"]
    errors = [e for e in engine.event_log.all() if e.error == "MISSING_DATA"]
    assert len(errors) == 1
    assert errors[0].phase is EventType.ORDER_REJECTED
    assert "non_trading_day" in errors[0].detail


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

    We use a zero-fee cost model so the identity is exact (realized pnl is
    always 0 and avg_cost is exactly 10.0000). Task 8 added fees; this test
    locks the conservation invariant under the no-fee special case.
    """

    from hqbacktest.engine.cost_model import Cost, CostModel
    from hqbacktest.engine.config import BacktestConfig
    from hqbacktest.engine.rule_set import DEFAULT_V01_RULES, TradingRuleSet

    class ZeroCostModel:
        def compute(self, order, price, quantity):
            return Cost(
                commission=Decimal("0"),
                stamp_tax=Decimal("0"),
                transfer_fee=Decimal("0"),
            )

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
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240105",
        initial_cash=Decimal("100000"),
        source="tushare",
        cost_model=ZeroCostModel(),
    )
    strategy = Alternating()
    engine = BacktestEngine(cfg, strategy=strategy, portal=p)
    engine.run()
    assert strategy.checked_days == 4
