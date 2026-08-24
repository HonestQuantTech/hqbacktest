"""Engine events and the immutable event log.

`EngineEvent` is the unit of the audit trail required by contract rule 10.
Every event carries the trading day, the phase and (optionally) the IDs of
the order / fill it relates to plus a free-form reason. The log itself is a
plain list; freezing it after `run()` returns keeps downstream code honest.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from ..domain.enums import EventType


@dataclass(frozen=True)
class EngineEvent:
    """One row of the engine audit trail."""

    date: str  # YYYYMMDD
    phase: EventType
    order_id: Optional[str] = None
    fill_id: Optional[str] = None
    error: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "phase": self.phase.name,
            "order_id": self.order_id,
            "fill_id": self.fill_id,
            "error": self.error,
            "detail": self.detail,
        }


class EventLog:
    """Append-only event log. Mutated by the engine, queried by tests / result."""

    def __init__(self) -> None:
        self._events: List[EngineEvent] = []

    def record(self, event: EngineEvent) -> None:
        self._events.append(event)

    def all(self) -> List[EngineEvent]:
        return list(self._events)

    def filter(
        self, *, phase: Optional[EventType] = None, date: Optional[str] = None
    ) -> List[EngineEvent]:
        out = []
        for event in self._events:
            if phase is not None and event.phase is not phase:
                continue
            if date is not None and event.date != date:
                continue
            out.append(event)
        return out

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)
