"""Phase scheduling for a single trading day.

The scheduler owns the visible_through settings per phase (contract §4) and
fires the strategy callbacks in the exact order required by the contract.
Open matching, fill recording and snapshot building are intentionally out of
scope (task 5 "不实现订单实际成交"); the scheduler records the phase
transitions in the event log so tests can assert the exact sequence.

`initialize` is NOT fired here: it is a run-level lifecycle callback invoked
exactly once by `BacktestEngine.run()` before the first trading day
(contract §4: `set_universe` may only happen there, and the universe is
immutable once the run starts).
"""

from dataclasses import dataclass
from typing import Optional

from ..data.data_view import DataView
from ..data.errors import MissingDataError
from ..data.portal import MarketDataPortal
from ..domain.enums import EventType
from .context import Context
from .events import EngineEvent, EventLog
from .strategy import Strategy


# Each phase's `visible_through` rule (contract §4). A value of `None` means
# "use the previous trading day" at run time.
PRE_BAR_VISIBLE_THROUGH = "PREVIOUS_TRADING_DAY"
SAME_DAY_VISIBLE_THROUGH = "SAME_DAY"

# Sentinel used as `visible_through` on the first trading day, when no prior
# trading day exists. It is a valid-format YYYYMMDD string strictly earlier
# than any real date, so the view is legal but exposes no bars.
NO_HISTORY_VISIBLE_THROUGH = "00000000"


@dataclass(frozen=True)
class PhaseSchedule:
    """Static description of one phase's visibility setting."""

    phase: EventType
    visible_through_mode: str  # "PREVIOUS_TRADING_DAY" | "SAME_DAY"


# Order matters; this is the canonical v0.1 schedule.
PHASE_SCHEDULE: tuple[PhaseSchedule, ...] = (
    PhaseSchedule(EventType.SESSION_START, PRE_BAR_VISIBLE_THROUGH),
    PhaseSchedule(EventType.BEFORE_TRADING_START, PRE_BAR_VISIBLE_THROUGH),
    PhaseSchedule(EventType.OPEN_MATCH, PRE_BAR_VISIBLE_THROUGH),
    PhaseSchedule(EventType.BAR_CLOSE, SAME_DAY_VISIBLE_THROUGH),
    PhaseSchedule(EventType.AFTER_TRADING_END, SAME_DAY_VISIBLE_THROUGH),
)


def previous_trading_day(portal: MarketDataPortal, date: str) -> Optional[str]:
    """Return the trading day strictly before `date`, or None if none exists.

    Only `MissingDataError` (the portal's documented "no such day" signal) is
    translated to None. Any other error (invalid input, corrupt snapshot, I/O)
    propagates: silently downgrading real data errors to "no history" would
    hide them from the audit trail.
    """
    try:
        return portal.previous_trading_day(date)
    except MissingDataError:
        return None


def build_view(
    portal: MarketDataPortal,
    schedule: PhaseSchedule,
    today: str,
) -> DataView:
    """Construct a `DataView` whose `visible_through` matches the phase rule."""
    if schedule.visible_through_mode == SAME_DAY_VISIBLE_THROUGH:
        visible_through = today
    elif schedule.visible_through_mode == PRE_BAR_VISIBLE_THROUGH:
        prev = previous_trading_day(portal, today)
        # On the first trading day no history exists yet; the sentinel keeps
        # the view legal but restricts reads to dates < today, so the
        # strategy simply sees no bars.
        visible_through = prev if prev is not None else NO_HISTORY_VISIBLE_THROUGH
    else:  # pragma: no cover - defensive
        raise ValueError(
            f"unknown visible_through mode: {schedule.visible_through_mode}"
        )
    return DataView(portal=portal, visible_through=visible_through)


def run_day(
    *,
    today: str,
    portal: MarketDataPortal,
    strategy: Strategy,
    context: Context,
    log: EventLog,
) -> None:
    """Fire the five phases for one trading day, in order."""
    context._set_date(today)
    for entry in PHASE_SCHEDULE:
        context._set_phase(entry.phase)
        if entry.phase is EventType.SESSION_START:
            # Contract §4: SESSION_START exposes no market data and fires no
            # strategy callback; it only marks the start of the day.
            context._set_data_view(None)
            log.record(
                EngineEvent(date=today, phase=entry.phase, detail="no data access")
            )
            continue
        view = build_view(portal, entry, today)
        context._set_data_view(view)
        # Phase start marker (always recorded, even for internal phases).
        log.record(
            EngineEvent(
                date=today,
                phase=entry.phase,
                detail=f"visible_through={view.visible_through}",
            )
        )
        if entry.phase is EventType.BEFORE_TRADING_START:
            strategy.before_trading_start(context, view)
        elif entry.phase is EventType.OPEN_MATCH:
            # Internal: no strategy callback in v0.1; the broker (task 7)
            # will plug in here. For now we just log the phase.
            continue
        elif entry.phase is EventType.BAR_CLOSE:
            strategy.on_bar(context, view)
        elif entry.phase is EventType.AFTER_TRADING_END:
            strategy.after_trading_end(context)
