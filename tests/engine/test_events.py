"""Tests for EngineEvent and EventLog."""

from hqbacktest.domain.enums import EventType
from hqbacktest.engine.events import EngineEvent, EventLog


def test_engine_event_carries_mandatory_fields():
    event = EngineEvent(date="20240102", phase=EventType.BAR_CLOSE)
    assert event.date == "20240102"
    assert event.phase is EventType.BAR_CLOSE
    assert event.order_id is None
    assert event.fill_id is None
    assert event.error is None
    assert event.detail == ""


def test_engine_event_is_frozen():
    event = EngineEvent(date="20240102", phase=EventType.BAR_CLOSE)
    import dataclasses

    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        event.date = "20240103"  # type: ignore[misc]


def test_engine_event_to_dict_uses_phase_name():
    event = EngineEvent(
        date="20240102",
        phase=EventType.OPEN_MATCH,
        order_id="O001",
        fill_id="F001",
        error="REJECTED",
        detail="no cash",
    )
    d = event.to_dict()
    assert d == {
        "date": "20240102",
        "phase": "OPEN_MATCH",
        "order_id": "O001",
        "fill_id": "F001",
        "error": "REJECTED",
        "detail": "no cash",
    }


def test_event_log_records_in_order():
    log = EventLog()
    log.record(EngineEvent(date="20240102", phase=EventType.SESSION_START))
    log.record(EngineEvent(date="20240102", phase=EventType.BEFORE_TRADING_START))
    log.record(EngineEvent(date="20240103", phase=EventType.SESSION_START))
    events = log.all()
    assert len(events) == 3
    assert events[0].date == "20240102"
    assert events[2].phase is EventType.SESSION_START


def test_event_log_filter_by_phase_and_date():
    log = EventLog()
    log.record(EngineEvent(date="20240102", phase=EventType.BAR_CLOSE))
    log.record(EngineEvent(date="20240102", phase=EventType.SESSION_START))
    log.record(EngineEvent(date="20240103", phase=EventType.BAR_CLOSE))

    bar_close_only = log.filter(phase=EventType.BAR_CLOSE)
    assert [e.date for e in bar_close_only] == ["20240102", "20240103"]

    d2_only = log.filter(date="20240102")
    assert len(d2_only) == 2
    assert d2_only[0].phase is EventType.BAR_CLOSE
    assert d2_only[1].phase is EventType.SESSION_START

    both = log.filter(phase=EventType.BAR_CLOSE, date="20240103")
    assert len(both) == 1


def test_event_log_is_iterable():
    log = EventLog()
    log.record(EngineEvent(date="20240102", phase=EventType.SESSION_START))
    assert [e.phase for e in log] == [EventType.SESSION_START]
