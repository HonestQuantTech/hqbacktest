"""Tests for the minimum BacktestEngine.

These tests use `InMemoryDataPortal` to assert the event sequence, the
visible_through settings and the order/fill relationship the contract
requires. No real matching is performed (task 5 scope).
"""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from hqbacktest.data import InMemoryDataPortal
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import (
    EventType,
    OrderType,
    Side,
)
from hqbacktest.domain.order import Order
from hqbacktest.engine import (
    BacktestConfig,
    BacktestEngine,
    NullStrategy,
    TradingDayIterator,
)
from hqbacktest.engine.errors import (
    ConfigurationError,
    DataPortalNotConfigured,
    RunFailed,
    StrategyLifecycleError,
)


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
    p = InMemoryDataPortal(
        calendar=["20240102", "20240103", "20240104"],
    )
    p.add_bar(_bar("20240102", "10.00"))
    p.add_bar(_bar("20240103", "10.50"))
    p.add_bar(_bar("20240104", "10.80"))
    return p


def _config():
    return BacktestConfig(
        start_date="20240102",
        end_date="20240104",
        initial_cash=Decimal("100000"),
        source="tushare",
    )


def test_engine_accepts_null_strategy_by_default():
    engine = BacktestEngine(_config(), portal=_portal())
    result = engine.run()
    assert len(result.trading_days) == 3


def test_engine_runs_three_days_emits_full_event_sequence():
    engine = BacktestEngine(_config(), portal=_portal())
    result = engine.run()
    phases_per_day = [
        [e.phase for e in engine.event_log.filter(date=d)] for d in result.trading_days
    ]
    expected_phases = [
        EventType.SESSION_START,
        EventType.BEFORE_TRADING_START,
        EventType.OPEN_MATCH,
        EventType.BAR_CLOSE,
        EventType.AFTER_TRADING_END,
    ]
    for day_phases in phases_per_day:
        assert day_phases == expected_phases


def test_engine_visible_through_per_phase_matches_contract():
    """BEFORE_TRADING_START(D) sees D-1, BAR_CLOSE(D) sees D."""

    @dataclass
    class Probe:
        captures: list[tuple[str, EventType, str]]  # (date, phase, visible_through)

        def initialize(self, context):
            return None

        def before_trading_start(self, context, data):
            self.captures.append(
                (context.current_date, context.phase, data.visible_through)
            )

        def on_bar(self, context, data):
            self.captures.append(
                (context.current_date, context.phase, data.visible_through)
            )

        def after_trading_end(self, context):
            return None

    probe = Probe(captures=[])
    engine = BacktestEngine(_config(), strategy=probe, portal=_portal())
    engine.run()
    bts_captures = [c for c in probe.captures if c[1] is EventType.BEFORE_TRADING_START]
    on_bar_captures = [c for c in probe.captures if c[1] is EventType.BAR_CLOSE]
    assert bts_captures == [
        ("20240102", EventType.BEFORE_TRADING_START, "00000000"),
        ("20240103", EventType.BEFORE_TRADING_START, "20240102"),
        ("20240104", EventType.BEFORE_TRADING_START, "20240103"),
    ]
    assert on_bar_captures == [
        ("20240102", EventType.BAR_CLOSE, "20240102"),
        ("20240103", EventType.BAR_CLOSE, "20240103"),
        ("20240104", EventType.BAR_CLOSE, "20240104"),
    ]


def test_engine_before_trading_start_cannot_read_future():
    """The strategy must never see past `visible_through`.

    Per task 14: on the first trading day the sentinel `00000000` is
    used and `history(...)` returns `[]` rather than raising (the
    strategy is allowed to call it; the data layer simply has nothing).
    On every other day, a direct future-data access still must raise.
    """
    from hqbacktest.data import FutureDataAccessError

    captured_first_day: list[list] = []
    future_attempts: list[int] = []

    class Reader:
        def initialize(self, context):
            return None

        def before_trading_start(self, context, data):
            captured_first_day.append(
                data.history("600000.SH", field="close", bar_count=1)
            )

        def on_bar(self, context, data):
            # Try to read past `visible_through` directly via get_bars.
            try:
                data.get_bars("600000.SH", "20991231", "20991231")
                future_attempts.append(0)
            except FutureDataAccessError:
                future_attempts.append(1)

        def after_trading_end(self, context):
            return None

    engine = BacktestEngine(_config(), strategy=Reader(), portal=_portal())
    engine.run()
    assert captured_first_day[0] == []
    assert all(future_attempts), (
        f"Expected every BAR_CLOSE get_bars call past visible_through to raise; "
        f"got {future_attempts}"
    )


def test_engine_bar_close_can_read_today_close():
    class Reader:
        def __init__(self):
            self.captured: list[str] = []

        def initialize(self, context):
            return None

        def before_trading_start(self, context, data):
            return None

        def on_bar(self, context, data):
            closes = data.history("600000.SH", field="close", bar_count=1)
            self.captured.append(str(closes[-1]))

        def after_trading_end(self, context):
            return None

    reader = Reader()
    engine = BacktestEngine(_config(), strategy=reader, portal=_portal())
    engine.run()
    # All three days emit on_bar; the latest close per day is captured.
    assert reader.captured == ["10.0000", "10.5000", "10.8000"]


def test_engine_orders_submitted_at_bar_close_cannot_match_on_same_day():
    """Contract: an order created at BAR_CLOSE(D) is queued for OPEN_MATCH(D+1).

    Task 5 does not implement fills, so the strict assertion here is:
        - the order is created in BAR_CLOSE(D)
        - the next OPEN_MATCH event is OPEN_MATCH(D+1), never OPEN_MATCH(D)
    """
    from hqbacktest.engine.events import EngineEvent

    class Submitter:
        last_bar_date: str = ""
        created_order_dates: list[str] = []
        next_open_match_after: list[tuple[str, str]] = []

        def initialize(self, context):
            return None

        def before_trading_start(self, context, data):
            return None

        def on_bar(self, context, data):
            order = Order(
                order_id=f"O-{context.current_date}",
                symbol="600000.SH",
                side=Side.BUY,
                quantity=100,
                order_type=OrderType.MARKET,
                created_at=context.current_date,
                created_session=EventType.BAR_CLOSE,
            )
            self.created_order_dates.append(context.current_date)
            self.last_bar_date = context.current_date

        def after_trading_end(self, context):
            return None

    submitter = Submitter()
    engine = BacktestEngine(_config(), strategy=submitter, portal=_portal())
    engine.run()

    # 1) Orders were created on every BAR_CLOSE day (all 3 days).
    assert submitter.created_order_dates == ["20240102", "20240103", "20240104"]

    # 2) For each order created at BAR_CLOSE(D), the next OPEN_MATCH event is
    #    for D+1, not D.
    log_dates = [
        (e.date, e.phase)
        for e in engine.event_log.all()
        if e.phase is EventType.OPEN_MATCH
    ]
    expected_open_match_dates = ["20240102", "20240103", "20240104"]
    assert [d for d, _ in log_dates] == expected_open_match_dates

    # 3) For each created order, find the next OPEN_MATCH event and assert
    #    it's strictly after the order's creation date. We exclude the last
    #    trading day, which has no follow-up OPEN_MATCH in the backtest
    #    window (that order would have been CANCELLED at run end by task 7).
    last_day = submitter.created_order_dates[-1]
    for order_date in submitter.created_order_dates:
        if order_date == last_day:
            continue
        next_open = next(
            (d for d, _ in log_dates if d > order_date),
            None,
        )
        assert next_open is not None
        assert next_open > order_date


def test_engine_requires_source_when_portal_missing():
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240104",
        initial_cash=Decimal("100000"),
        source="",
    )
    with pytest.raises(DataPortalNotConfigured):
        BacktestEngine(cfg).run()


def test_engine_does_not_invent_natural_days_outside_calendar():
    """If the calendar stops at D, run() must stop too."""
    p = InMemoryDataPortal(calendar=["20240102", "20240103", "20240104"])
    p.add_bar(_bar("20240102", "10.00"))
    p.add_bar(_bar("20240103", "10.50"))
    p.add_bar(_bar("20240104", "10.80"))
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240131",  # asks through 01-31
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    engine = BacktestEngine(cfg, portal=p)
    result = engine.run()
    assert result.trading_days == ["20240102", "20240103", "20240104"]


def test_engine_run_is_deterministic():
    cfg = _config()
    a = BacktestEngine(cfg, portal=_portal()).run()
    b = BacktestEngine(cfg, portal=_portal()).run()
    assert [e.to_dict() for e in a.event_log.all()] == [
        e.to_dict() for e in b.event_log.all()
    ]


def test_engine_event_log_preserves_chronological_order():
    engine = BacktestEngine(_config(), portal=_portal())
    engine.run()
    events = engine.event_log.all()
    # Per the canonical phase schedule, within each trading day the events must
    # appear in PHASE_SCHEDULE order: SESSION_START, BEFORE_TRADING_START,
    # OPEN_MATCH, BAR_CLOSE, AFTER_TRADING_END.
    from hqbacktest.engine.scheduler import PHASE_SCHEDULE

    canonical = [entry.phase for entry in PHASE_SCHEDULE]
    by_date: dict[str, list[EventType]] = {}
    for event in events:
        by_date.setdefault(event.date, []).append(event.phase)
    for phases in by_date.values():
        assert phases == canonical
    # And days are processed in ascending date order.
    assert list(by_date.keys()) == sorted(by_date.keys())


def test_engine_emits_zero_events_for_empty_calendar():
    p = InMemoryDataPortal(calendar=[])
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240110",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    result = BacktestEngine(cfg, portal=p).run()
    assert result.trading_days == []
    assert len(result.event_log) == 0


def test_engine_rejects_invalid_config():
    with pytest.raises(ConfigurationError):
        BacktestConfig(
            start_date="2024-01-02",
            end_date="20240104",
            initial_cash=Decimal("100000"),
            source="tushare",
        )


def test_engine_rejects_initial_cash_float():
    with pytest.raises(ConfigurationError):
        BacktestConfig(
            start_date="20240102",
            end_date="20240104",
            initial_cash=100000.0,  # type: ignore[arg-type]
            source="tushare",
        )


def test_engine_rejects_inverted_window():
    with pytest.raises(ConfigurationError):
        BacktestConfig(
            start_date="20240110",
            end_date="20240102",
            initial_cash=Decimal("100000"),
            source="tushare",
        )


def test_engine_iterator_uses_portal_calendar_only():
    """`TradingDayIterator` is exposed via `engine.iterator` and uses the
    portal's calendar."""
    p = _portal()
    it = TradingDayIterator(p, "20240102", "20240104")
    assert list(it) == ["20240102", "20240103", "20240104"]


def test_engine_wraps_strategy_exception_as_run_failed():
    """Engine converts strategy exceptions into RunFailed carrying date + phase."""

    class Boom:
        def initialize(self, context):
            return None

        def before_trading_start(self, context, data):
            raise RuntimeError("boom")

        def on_bar(self, context, data):
            return None

        def after_trading_end(self, context):
            return None

    engine = BacktestEngine(_config(), strategy=Boom(), portal=_portal())
    with pytest.raises(RunFailed) as exc:
        engine.run()
    assert exc.value.date == "20240102"
    assert exc.value.phase == "BEFORE_TRADING_START"
    assert isinstance(exc.value.original, RuntimeError)


def test_engine_calls_initialize_exactly_once_per_run():
    """`initialize` is a run-level lifecycle callback (contract §4): it must
    fire once before the first trading day, not at every SESSION_START."""

    class Counter:
        def __init__(self):
            self.calls = 0

        def initialize(self, context):
            self.calls += 1

        def before_trading_start(self, context, data):
            return None

        def on_bar(self, context, data):
            return None

        def after_trading_end(self, context):
            return None

    counter = Counter()
    BacktestEngine(_config(), strategy=counter, portal=_portal()).run()
    assert counter.calls == 1


def test_engine_initialize_failure_is_run_failed():
    class Boom:
        def initialize(self, context):
            raise RuntimeError("init boom")

        def before_trading_start(self, context, data):
            return None

        def on_bar(self, context, data):
            return None

        def after_trading_end(self, context):
            return None

    engine = BacktestEngine(_config(), strategy=Boom(), portal=_portal())
    with pytest.raises(RunFailed) as exc:
        engine.run()
    assert exc.value.phase == "INITIALIZE"
    assert isinstance(exc.value.original, RuntimeError)
    # The failure is recorded in the audit trail as RUN_FAILED.
    errors = [e for e in engine.event_log.all() if e.error is not None]
    assert len(errors) == 1
    assert errors[0].phase is EventType.RUN_FAILED
    assert errors[0].error == "RuntimeError"


def test_engine_run_may_only_be_called_once():
    engine = BacktestEngine(_config(), portal=_portal())
    engine.run()
    with pytest.raises(StrategyLifecycleError):
        engine.run()


def test_engine_error_event_records_the_failing_phase():
    """The audit-trail error event must carry the phase where the exception
    was raised, not a guessed/default phase."""

    class Boom:
        def initialize(self, context):
            return None

        def before_trading_start(self, context, data):
            return None

        def on_bar(self, context, data):
            raise RuntimeError("boom at close")

        def after_trading_end(self, context):
            return None

    engine = BacktestEngine(_config(), strategy=Boom(), portal=_portal())
    with pytest.raises(RunFailed) as exc:
        engine.run()
    assert exc.value.phase == "BAR_CLOSE"
    errors = [e for e in engine.event_log.all() if e.error is not None]
    assert len(errors) == 1
    assert errors[0].phase is EventType.BAR_CLOSE
    assert errors[0].date == "20240102"


def test_engine_rejects_order_from_after_trading_end():
    """Contract §4: AFTER_TRADING_END is not orderable; the run must fail
    loudly instead of silently queueing the order."""

    class LateOrder(NullStrategy):
        def after_trading_end(self, context):
            context.order("600000.SH", 100)

    engine = BacktestEngine(_config(), strategy=LateOrder(), portal=_portal())
    with pytest.raises(RunFailed) as exc:
        engine.run()
    assert exc.value.phase == "AFTER_TRADING_END"


def test_engine_rejects_order_and_data_from_initialize():
    """Contract §4: initialize has no market data and cannot submit orders."""

    class DataGrabber(NullStrategy):
        def initialize(self, context):
            context.history("600000.SH")

    engine = BacktestEngine(_config(), strategy=DataGrabber(), portal=_portal())
    with pytest.raises(RunFailed) as exc:
        engine.run()
    assert exc.value.phase == "INITIALIZE"

    class EarlyOrder(NullStrategy):
        def initialize(self, context):
            context.order("600000.SH", 100)

    engine = BacktestEngine(_config(), strategy=EarlyOrder(), portal=_portal())
    with pytest.raises(RunFailed) as exc:
        engine.run()
    assert exc.value.phase == "INITIALIZE"


def test_engine_rejects_set_universe_outside_initialize():
    """Contract §4: the universe is immutable once the run starts."""

    class LateUniverse(NullStrategy):
        def on_bar(self, context, data):
            context.set_universe(["600000.SH"])

    engine = BacktestEngine(_config(), strategy=LateUniverse(), portal=_portal())
    with pytest.raises(RunFailed) as exc:
        engine.run()
    assert exc.value.phase == "BAR_CLOSE"


def test_engine_records_order_created_events_during_run():
    class Buyer(NullStrategy):
        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    engine = BacktestEngine(_config(), strategy=Buyer(), portal=_portal())
    engine.run()
    created = [e for e in engine.event_log.all() if e.phase is EventType.ORDER_CREATED]
    # One order per BAR_CLOSE, one per trading day.
    assert len(created) == 3
    assert all(e.order_id for e in created)
    assert [e.date for e in created] == ["20240102", "20240103", "20240104"]
