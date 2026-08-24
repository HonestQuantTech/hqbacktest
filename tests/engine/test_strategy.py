"""Tests for BaseStrategy and the NullStrategy fallback."""

from dataclasses import dataclass, field
from decimal import Decimal

import pytest

from hqbacktest.data import DataView, InMemoryDataPortal
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import EventType
from hqbacktest.domain.portfolio import Portfolio
from hqbacktest.engine.config import BacktestConfig
from hqbacktest.engine.context import Context
from hqbacktest.engine.engine import BacktestEngine
from hqbacktest.engine.events import EventLog
from hqbacktest.engine.strategy import BaseStrategy, NullStrategy


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


def test_null_strategy_runs_clean():
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240103",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    engine = BacktestEngine(cfg, strategy=NullStrategy(), portal=_portal())
    result = engine.run()
    assert len(result.trading_days) == 2


def test_base_strategy_default_callbacks_do_nothing():
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240103",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    engine = BacktestEngine(cfg, strategy=BaseStrategy(), portal=_portal())
    result = engine.run()
    assert len(result.trading_days) == 2


def test_base_strategy_subclass_runs_in_engine():
    @dataclass
    class BuyAndHold(BaseStrategy):
        seen_dates: list = field(default_factory=list)

        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def before_trading_start(self, context, data):
            self.seen_dates.append(("BEFORE", context.now))

        def on_bar(self, context, data):
            self.seen_dates.append(("BAR_CLOSE", context.now))
            context.order("600000.SH", 100)

        def after_trading_end(self, context):
            self.seen_dates.append(("AFTER", context.now))

    strategy = BuyAndHold()
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240103",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    engine = BacktestEngine(cfg, strategy=strategy, portal=_portal())
    result = engine.run()
    # BEFORE_TRADING_START fires for every day (including the first).
    assert ("BEFORE", "20240102") in strategy.seen_dates
    assert ("BEFORE", "20240103") in strategy.seen_dates
    # on_bar fires for every day in the schedule.
    assert ("BAR_CLOSE", "20240102") in strategy.seen_dates
    assert ("BAR_CLOSE", "20240103") in strategy.seen_dates
    # AFTER_TRADING_END fires too.
    assert ("AFTER", "20240102") in strategy.seen_dates


def test_base_strategy_log_helper_appends_engine_event():
    portfolio = Portfolio(initial_cash=Decimal("100000"))
    log = EventLog()
    ctx = Context(
        current_date="20240102",
        portfolio=portfolio,
        event_log=log,
        data_view=DataView(portal=_portal(), visible_through="20240102"),
    )
    ctx._mark_initialized()
    ctx._set_phase(EventType.BEFORE_TRADING_START)
    strategy = BaseStrategy()
    strategy.log(ctx, "hello")
    assert len(log) == 1
    events = log.all()
    assert events[0].detail == "hello"
    assert events[0].phase is EventType.BEFORE_TRADING_START


def test_engine_runs_with_base_strategy_declaring_universe():
    @dataclass
    class UniverseStrategy(BaseStrategy):
        seen: list = field(default_factory=list)

        def initialize(self, context):
            context.set_universe(["600000.SH"])
            self.seen.append(context.universe())

    strategy = UniverseStrategy()
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240102",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    engine = BacktestEngine(cfg, strategy=strategy, portal=_portal())
    engine.run()
    assert strategy.seen == [["600000.SH"]]
