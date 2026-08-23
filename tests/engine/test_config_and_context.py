"""Tests for engine-level configuration errors and the Context stub."""

from decimal import Decimal

import pytest

from hqbacktest.data import DataView, InMemoryDataPortal
from hqbacktest.domain.enums import EventType
from hqbacktest.domain.portfolio import Portfolio
from hqbacktest.engine.config import BacktestConfig
from hqbacktest.engine.context import Context
from hqbacktest.engine.errors import ConfigurationError
from hqbacktest.engine.events import EngineEvent, EventLog


def test_config_accepts_str_initial_cash():
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240104",
        initial_cash="100000",
        source="tushare",
    )
    assert cfg.initial_cash == 100000


def test_config_rejects_invalid_initial_cash_string():
    with pytest.raises(ConfigurationError):
        BacktestConfig(
            start_date="20240102",
            end_date="20240104",
            initial_cash="not-a-number",
            source="tushare",
        )


def test_config_rejects_negative_initial_cash():
    with pytest.raises(ConfigurationError):
        BacktestConfig(
            start_date="20240102",
            end_date="20240104",
            initial_cash="-1",
            source="tushare",
        )


def test_config_default_data_root_is_hqdata_default():
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240104",
        initial_cash="100000",
        source="tushare",
    )
    assert cfg.data_root == "~/.hqdata"


def test_context_exposes_portfolio_and_log():
    portfolio = Portfolio(initial_cash=Decimal("100000"))
    log = EventLog()
    portal = InMemoryDataPortal(calendar=["20240102"])
    context = Context(
        current_date="20240102",
        portfolio=portfolio,
        event_log=log,
        data_view=DataView(portal=portal, visible_through="20240102"),
    )
    context._mark_initialized()
    context._set_phase(EventType.BEFORE_TRADING_START)
    assert context.cash() == Decimal("100000")
    assert context.phase is EventType.BEFORE_TRADING_START
    assert context.visible_through == "20240102"


def test_context_phase_and_date_are_read_only_for_strategies():
    """Task 6 isolation: strategy code must not be able to forge the current
    date or phase (they drive order IDs and `created_session`)."""
    portfolio = Portfolio(initial_cash=Decimal("100000"))
    log = EventLog()
    portal = InMemoryDataPortal(calendar=["20240102"])
    context = Context(
        current_date="20240102",
        portfolio=portfolio,
        event_log=log,
        data_view=DataView(portal=portal, visible_through="20240102"),
    )
    with pytest.raises(AttributeError):
        context.phase = EventType.BAR_CLOSE  # type: ignore[misc]
    with pytest.raises(AttributeError):
        context.current_date = "20240103"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        context.visible_through = "20240103"  # type: ignore[misc]


def test_context_records_events_through_event_log():
    portfolio = Portfolio(initial_cash=Decimal("100000"))
    log = EventLog()
    portal = InMemoryDataPortal(calendar=["20240102"])
    context = Context(
        current_date="20240102",
        portfolio=portfolio,
        event_log=log,
        data_view=DataView(portal=portal, visible_through="20240102"),
    )
    context._mark_initialized()
    context._set_phase(EventType.BAR_CLOSE)
    context.record_event(
        EngineEvent(
            date="20240102",
            phase=EventType.BAR_CLOSE,
        )
    )
    assert len(log) == 1
