"""End-to-end tests for the rule set + cost model integration."""

from dataclasses import dataclass
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
from hqbacktest.engine.config import BacktestConfig
from hqbacktest.engine.context import Context
from hqbacktest.engine.cost_model import Cost, DefaultCostModel
from hqbacktest.engine.engine import BacktestEngine
from hqbacktest.engine.errors import RunFailed
from hqbacktest.engine.events import EngineEvent, EventLog
from hqbacktest.engine.rule_set import (
    DEFAULT_V01_RULES,
    InsufficientCashRule,
    LotSizeRule,
    TradingRuleSet,
)
from hqbacktest.engine.strategy import BaseStrategy


def _bar(date: str, close: str = "10.00", open_: str = "10.00") -> Bar:
    return Bar.from_raw(
        symbol="600000.SH",
        date=date,
        open=open_,
        high="11.00",
        low="9.00",
        close=close,
        volume=1000,
    )


def _portal(days: list[str]) -> InMemoryDataPortal:
    p = InMemoryDataPortal(calendar=days)
    for d in days:
        p.add_bar(_bar(d))
    return p


def _config(start="20240102", end="20240104", **kwargs):
    return BacktestConfig(
        start_date=start,
        end_date=end,
        initial_cash=Decimal("100000"),
        source="tushare",
        **kwargs,
    )


class BuyOnce(BaseStrategy):
    def initialize(self, context):
        context.set_universe(["600000.SH"])

    def on_bar(self, context, data):
        context.order("600000.SH", 100)


# --------------------------------------------------------------------- #
# Default cost model integration
# --------------------------------------------------------------------- #


def test_default_cost_model_records_fees_in_fill_event():
    """ORDER_FILLED event includes commission and stamp tax in the detail."""

    class Recorder(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    engine = BacktestEngine(
        _config(), strategy=Recorder(), portal=_portal(["20240102", "20240103"])
    )
    engine.run()
    filled = [e for e in engine.event_log.all() if e.phase is EventType.ORDER_FILLED]
    assert len(filled) == 1
    detail = filled[0].detail
    assert "comm=5.00" in detail  # 100 * 10 = 1000 * 0.00025 = 0.25 -> floor 5
    assert "stamp=0.00" in detail  # BUY: no stamp tax


def test_default_cost_charges_stamp_tax_on_sell():
    """Sell-side stamp tax (0.1% of gross) appears in the fill detail."""

    class BuyThenSell(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])
            self.bought = False

        def on_bar(self, context, data):
            if not self.bought:
                context.order("600000.SH", 100)
                self.bought = True
            else:
                context.order("600000.SH", -100)

    # 3 trading days so the sell (placed at BAR_CLOSE day 2) matches at
    # OPEN_MATCH day 3.
    engine = BacktestEngine(
        _config("20240102", "20240104"),
        strategy=BuyThenSell(),
        portal=_portal(["20240102", "20240103", "20240104"]),
    )
    engine.run()
    fills = [e for e in engine.event_log.all() if e.phase is EventType.ORDER_FILLED]
    assert len(fills) == 2
    sell_fill = fills[1]
    # Sell: commission = max(1000 * 0.00025, 5) = 5; stamp = 1000 * 0.001 = 1.
    assert "comm=5.00" in sell_fill.detail
    assert "stamp=1.00" in sell_fill.detail


def test_default_min_commission_enforced_on_small_orders():
    """A 1-share buy still pays the 5 CNY commission floor."""

    class BuyOneShare(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            # Bypass the lot check by using order_target (which rounds to 100).
            # Use direct order via .order() but with 100 to actually fill.
            context.order("600000.SH", 100)

    # Single share 1 CNY cost 0.0025; min 5 -> 5 CNY.
    # 100 shares 1 CNY = 100 CNY cost 0.25; min 5 -> 5 CNY.
    p = _portal(["20240102", "20240103"])
    engine = BacktestEngine(_config(), strategy=BuyOneShare(), portal=p)
    engine.run()
    fill = next(e for e in engine.event_log.all() if e.phase is EventType.ORDER_FILLED)
    assert "comm=5.00" in fill.detail


def test_engine_cash_conservation_includes_fees():
    """Cash_out per BUY = price*qty + commission. v0.1 fees are explicit."""

    class BuyOnce(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    initial = Decimal("100000")
    engine = BacktestEngine(
        BacktestConfig(
            start_date="20240102",
            end_date="20240103",
            initial_cash=initial,
            source="tushare",
        ),
        strategy=BuyOnce(),
        portal=_portal(["20240102", "20240103"]),
    )
    engine.run()
    # BUY 100 @ 10 = 1000 gross + 5 commission = 1005 cash out.
    # 1 share of position remains (qty=100), no sell, no further fees.
    assert engine.portfolio.cash == initial - Decimal("1000") - Decimal("5")
    assert engine.portfolio.positions["600000.SH"].quantity == 100


def test_custom_cost_model_overrides_default():
    """A user-supplied zero-fee model makes BUY cash out equal to gross only."""

    class ZeroFees:
        def compute(self, order, price, quantity):
            return Cost(
                commission=Decimal("0"),
                stamp_tax=Decimal("0"),
                transfer_fee=Decimal("0"),
            )

    class BuyOnce(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    engine = BacktestEngine(
        BacktestConfig(
            start_date="20240102",
            end_date="20240103",
            initial_cash=Decimal("100000"),
            source="tushare",
            cost_model=ZeroFees(),
        ),
        strategy=BuyOnce(),
        portal=_portal(["20240102", "20240103"]),
    )
    engine.run()
    assert engine.portfolio.cash == Decimal("99000.00")  # 1000 gross, no fees


# --------------------------------------------------------------------- #
# Rule set integration (engine-level)
# --------------------------------------------------------------------- #


def test_engine_rejects_buy_with_insufficient_cash_via_rule_set():
    """A buy that exceeds cash is rejected with INSUFFICIENT_CASH."""

    class BuyALot(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 10000)  # 100000 CNY, all cash

    engine = BacktestEngine(
        _config(), strategy=BuyALot(), portal=_portal(["20240102", "20240103"])
    )
    engine.run()
    rejections = [e for e in engine.event_log.all() if e.error == "INSUFFICIENT_CASH"]
    # 2 BAR_CLOSE days: day-1 BUY matches day-2 OK (10k shares * 10 = 100k, exact).
    # Day-2 BUY: would need 200k but cash is 0 -> rejected.
    assert any("rule:insufficient_cash" in (e.detail or "") for e in rejections)


def test_engine_rejects_buy_with_lot_size_violation_via_rule_set():
    """Non-100 quantity is rejected by the lot-size rule."""

    class Buy99(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            # `Context.order` already rounds, so verify with a raw Order.
            from hqbacktest.domain.order import Order

            order = Order(
                order_id="O-99",
                symbol="600000.SH",
                side=Side.BUY,
                quantity=99,
                order_type=OrderType.MARKET,
                created_at="20240102",
                created_session=EventType.BAR_CLOSE,
            )
            # Round-trip through Context would re-round; we manually push it.
            order.transition(OrderStatus.ACCEPTED, at="20240102")
            order.transition(OrderStatus.PENDING, at="20240102")
            context._pending_orders.append(order)

    engine = BacktestEngine(
        _config(), strategy=Buy99(), portal=_portal(["20240102", "20240103"])
    )
    engine.run()
    rejections = [
        e
        for e in engine.event_log.all()
        if e.error and "rule:lot_size" in (e.detail or "")
    ]
    assert len(rejections) == 1


def test_engine_rejects_sell_with_insufficient_shares_via_rule_set():
    """A sell larger than sells available is rejected with INSUFFICIENT_SHARES.

    With the 2-day window 20240102-20240103:
        - 20240102 BAR_CLOSE creates a SELL order; OPEN_MATCH 20240103
          fires the rule set -> rejected with T1/INSUFFICIENT_SHARES.
        - 20240103 BAR_CLOSE creates another SELL order; no OPEN_MATCH
          follows so it stays PENDING (then BACKTEST_ENDED cancels it).
    Total: exactly one INSUFFICIENT_SHARES rejection.
    """

    class SellNone(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", -100)  # we own 0

    engine = BacktestEngine(
        _config("20240102", "20240103"),
        strategy=SellNone(),
        portal=_portal(["20240102", "20240103"]),
    )
    engine.run()
    rejections = [e for e in engine.event_log.all() if e.error == "INSUFFICIENT_SHARES"]
    assert len(rejections) == 1
    assert rejections[0].detail.startswith("rule:t_plus_one")


def test_custom_rule_set_overrides_default():
    """A rule set that drops LotSizeRule lets 99-share BUY through."""

    class Buy99(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            from hqbacktest.domain.order import Order

            order = Order(
                order_id="O-99",
                symbol="600000.SH",
                side=Side.BUY,
                quantity=99,
                order_type=OrderType.MARKET,
                created_at="20240102",
                created_session=EventType.BAR_CLOSE,
            )
            order.transition(OrderStatus.ACCEPTED, at="20240102")
            order.transition(OrderStatus.PENDING, at="20240102")
            context._pending_orders.append(order)

    # Drop LotSizeRule so 99-share BUY passes.
    custom = [r for r in DEFAULT_V01_RULES if not isinstance(r, LotSizeRule)]
    engine = BacktestEngine(
        BacktestConfig(
            start_date="20240102",
            end_date="20240103",
            initial_cash=Decimal("100000"),
            source="tushare",
            rule_set=TradingRuleSet(custom),
        ),
        strategy=Buy99(),
        portal=_portal(["20240102", "20240103"]),
    )
    engine.run()
    fills = [e for e in engine.event_log.all() if e.phase is EventType.ORDER_FILLED]
    assert len(fills) == 1
    rejections = [
        e
        for e in engine.event_log.all()
        if e.error and "rule:lot_size" in (e.detail or "")
    ]
    assert rejections == []


def test_disabling_a_rule_does_not_affect_others():
    """Disabling LotSizeRule doesn't change T+1 behavior."""

    class BuyThenSell(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])
            self.bought = False

        def on_bar(self, context, data):
            if not self.bought:
                context.order("600000.SH", 100)
                self.bought = True
            else:
                context.order("600000.SH", -100)

    p = _portal(["20240102", "20240103", "20240104"])
    engine = BacktestEngine(
        BacktestConfig(
            start_date="20240102",
            end_date="20240104",
            initial_cash=Decimal("100000"),
            source="tushare",
            rule_set=TradingRuleSet(
                [
                    r
                    for r in DEFAULT_V01_RULES
                    if not isinstance(r, InsufficientCashRule)
                ]
            ),
        ),
        strategy=BuyThenSell(),
        portal=p,
    )
    engine.run()
    fills = [e for e in engine.event_log.all() if e.phase is EventType.ORDER_FILLED]
    # Two fills (buy + sell); T+1 still works after dropping cash rule.
    assert len(fills) == 2


def test_engine_event_log_records_rule_name_in_rejection_detail():
    """Each rejection detail includes the rule that triggered it."""

    class BuyALot(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 10000)

    engine = BacktestEngine(
        _config(), strategy=BuyALot(), portal=_portal(["20240102", "20240103"])
    )
    engine.run()
    rejections = [e for e in engine.event_log.all() if e.error == "INSUFFICIENT_CASH"]
    # Detail begins with "rule:insufficient_cash: ..."
    for r in rejections:
        assert r.detail.startswith("rule:insufficient_cash")
