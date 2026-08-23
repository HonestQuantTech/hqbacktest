"""Minimal Context for task 5.

Task 6 expands this with the full read-only API (cash, positions, universe,
pending orders, etc.). For the event clock we only need:
    - `current_date`: the trading day being processed.
    - `event_log`: the audit trail the strategy / tests can inspect.
    - `portfolio`: the shared ledger (read-only for now).
"""

from dataclasses import dataclass
from typing import Optional

from ..domain.enums import EventType
from ..domain.portfolio import Portfolio
from .events import EventLog


@dataclass
class Context:
    """Read-only façade exposed to strategies during a backtest."""

    current_date: str  # YYYYMMDD
    portfolio: Portfolio
    event_log: EventLog
    phase: Optional[EventType] = None
    visible_through: str = ""

    def record_event(self, event) -> None:
        """Convenience for strategies/tests that want to add their own entries."""
        self.event_log.record(event)

    # ------------------------------------------------------------------ #
    # Read-only accessors used in later tasks
    # ------------------------------------------------------------------ #

    def cash(self):
        return self.portfolio.cash

    def is_window_before_open(self) -> bool:
        return self.phase is EventType.BEFORE_TRADING_START
