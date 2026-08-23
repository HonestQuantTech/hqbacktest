"""Tests for engine-level configuration errors and the Context stub."""

from decimal import Decimal

import pytest

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
    context = Context(
        current_date="20240102",
        portfolio=portfolio,
        event_log=log,
        phase=EventType.BEFORE_TRADING_START,
        visible_through="20240101",
    )
    assert context.cash() == Decimal("100000")
    assert context.is_window_before_open() is True


def test_context_records_events_through_event_log():
    portfolio = Portfolio(initial_cash=Decimal("100000"))
    log = EventLog()
    context = Context(
        current_date="20240102",
        portfolio=portfolio,
        event_log=log,
    )
    context.record_event(
        EngineEvent(
            date="20240102",
            phase=EventType.BAR_CLOSE,
        )
    )
    assert len(log) == 1
