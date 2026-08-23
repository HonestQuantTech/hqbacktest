"""Tests for the phase scheduler (visible_through per phase)."""

from decimal import Decimal

import pytest

from hqbacktest.data import DataView, InMemoryDataPortal
from hqbacktest.data.errors import FutureDataAccessError, InvalidDataError
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import EventType
from hqbacktest.domain.portfolio import Portfolio
from hqbacktest.engine.context import Context
from hqbacktest.engine.events import EventLog
from hqbacktest.engine.scheduler import (
    PHASE_SCHEDULE,
    build_view,
    previous_trading_day,
    run_day,
)
from hqbacktest.engine.strategy import NullStrategy


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


def _portal_with_three_days():
    p = InMemoryDataPortal(
        calendar=["20240102", "20240103", "20240104"],
    )
    p.add_bar(_bar("20240102", "10.00"))
    p.add_bar(_bar("20240103", "10.50"))
    p.add_bar(_bar("20240104", "10.80"))
    return p


def _context(log: EventLog, *, portal=None, today="20240103") -> Context:
    if portal is None:
        portal = _portal_with_three_days()
    return Context(
        current_date=today,
        portfolio=Portfolio(initial_cash=Decimal("100000")),
        event_log=log,
        data_view=DataView(portal=portal, visible_through=today),
    )


def test_phase_schedule_canonical_order():
    phases = [entry.phase for entry in PHASE_SCHEDULE]
    assert phases == [
        EventType.SESSION_START,
        EventType.BEFORE_TRADING_START,
        EventType.OPEN_MATCH,
        EventType.BAR_CLOSE,
        EventType.AFTER_TRADING_END,
    ]


def test_build_view_same_day_phase_uses_today():
    p = _portal_with_three_days()
    view = build_view(
        p,
        PHASE_SCHEDULE[3],  # BAR_CLOSE
        today="20240103",
    )
    assert view.visible_through == "20240103"
    closes = view.history("600000.SH", field="close", bar_count=1)
    assert [str(c) for c in closes] == ["10.5000"]


def test_build_view_pre_bar_phase_uses_previous_day():
    p = _portal_with_three_days()
    view = build_view(
        p,
        PHASE_SCHEDULE[1],  # BEFORE_TRADING_START
        today="20240103",
    )
    assert view.visible_through == "20240102"
    closes = view.history("600000.SH", field="close", bar_count=1)
    assert [str(c) for c in closes] == ["10.0000"]


def test_pre_bar_view_cannot_see_today():
    """Contract verification: BEFORE_TRADING_START cannot read D's bar."""
    p = _portal_with_three_days()
    view = build_view(p, PHASE_SCHEDULE[1], today="20240103")
    with pytest.raises(FutureDataAccessError):
        view.get_bars("600000.SH", "20240103", "20240103")


def test_same_day_view_can_see_today():
    """Contract verification: BAR_CLOSE can read D's bar."""
    p = _portal_with_three_days()
    view = build_view(p, PHASE_SCHEDULE[3], today="20240103")
    closes = view.history("600000.SH", field="close", bar_count=1)
    assert [str(c) for c in closes] == ["10.5000"]


def test_run_day_emits_five_events_in_order():
    portal = _portal_with_three_days()
    log = EventLog()
    run_day(
        today="20240103",
        portal=portal,
        strategy=NullStrategy(),
        context=_context(log),
        log=log,
    )
    events = log.all()
    assert len(events) == 5
    assert [e.phase for e in events] == [
        EventType.SESSION_START,
        EventType.BEFORE_TRADING_START,
        EventType.OPEN_MATCH,
        EventType.BAR_CLOSE,
        EventType.AFTER_TRADING_END,
    ]
    assert all(e.date == "20240103" for e in events)


def test_run_day_does_not_call_initialize():
    """`initialize` belongs to the engine's run-level lifecycle; run_day must
    never fire it, not even at SESSION_START."""

    class Counter(NullStrategy):
        def __init__(self):
            self.calls = 0

        def initialize(self, context):
            self.calls += 1

    counter = Counter()
    run_day(
        today="20240103",
        portal=_portal_with_three_days(),
        strategy=counter,
        context=_context(EventLog()),
        log=EventLog(),
    )
    assert counter.calls == 0


def test_run_day_records_visible_through_in_detail():
    portal = _portal_with_three_days()
    log = EventLog()
    run_day(
        today="20240103",
        portal=portal,
        strategy=NullStrategy(),
        context=_context(log),
        log=log,
    )
    events = {e.phase: e.detail for e in log.all()}
    # SESSION_START exposes no market data at all (contract §4).
    assert events[EventType.SESSION_START] == "no data access"
    assert "visible_through=20240102" in events[EventType.BEFORE_TRADING_START]
    assert "visible_through=20240103" in events[EventType.BAR_CLOSE]
    assert "visible_through=20240103" in events[EventType.AFTER_TRADING_END]


def test_previous_trading_day_returns_none_for_first_day():
    p = InMemoryDataPortal(calendar=["20240102"])
    assert previous_trading_day(p, "20240102") is None


def test_previous_trading_day_returns_prior_open_day():
    p = InMemoryDataPortal(
        calendar=["20240102", "20240103", "20240105"],
    )
    assert previous_trading_day(p, "20240105") == "20240103"


def test_previous_trading_day_propagates_unexpected_errors():
    """Only MissingDataError means "no previous day"; real data errors must
    surface instead of being silently downgraded to an empty-history view."""

    class BrokenPortal:
        def previous_trading_day(self, date: str) -> str:
            raise InvalidDataError("calendar", "corrupt snapshot")

    with pytest.raises(InvalidDataError):
        previous_trading_day(BrokenPortal(), "20240103")  # type: ignore[arg-type]


def test_run_day_propagates_strategy_exception_directly():
    """Direct calls to `run_day` surface raw strategy exceptions so that the
    engine's wrapping layer can attach date / phase metadata."""

    class Boom(NullStrategy):
        def before_trading_start(self, context, data):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError) as exc:
        run_day(
            today="20240103",
            portal=_portal_with_three_days(),
            strategy=Boom(),
            context=_context(EventLog()),
            log=EventLog(),
        )
    assert str(exc.value) == "boom"
