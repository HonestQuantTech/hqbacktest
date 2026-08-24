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
from typing import Callable, List, Optional

from ..data.data_view import DataView
from ..data.errors import MissingDataError
from ..data.portal import MarketDataPortal
from ..domain.enums import EventType
from ..domain.order import Order
from .context import Context
from .events import EngineEvent, EventLog
from .strategy import Strategy


# Callback the engine plugs in to consume pending orders at OPEN_MATCH.
OpenMatchCallback = Callable[[str, List[Order]], None]


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
    """Construct a `DataView` whose `visible_through` matches the phase rule.

    `universe_start` is deliberately left unset here: it denotes the earliest
    date the strategy may query (a run-level bound derived from the backtest
    window), not the phase visibility. Bounding `history(bar_count=N)`'s
    lookback window is `DataView`'s own responsibility (task 14), independent
    of per-phase visibility.
    """
    if schedule.visible_through_mode == SAME_DAY_VISIBLE_THROUGH:
        return DataView(portal=portal, visible_through=today)
    if schedule.visible_through_mode == PRE_BAR_VISIBLE_THROUGH:
        prev = previous_trading_day(portal, today)
        if prev is None:
            # First trading day: no history exists yet. The sentinel keeps
            # the view legal but exposes no data.
            return DataView(
                portal=portal,
                visible_through=NO_HISTORY_VISIBLE_THROUGH,
            )
        return DataView(portal=portal, visible_through=prev)
    raise ValueError(f"unknown visible_through mode: {schedule.visible_through_mode}")


def run_day(
    *,
    today: str,
    portal: MarketDataPortal,
    strategy: Strategy,
    context: Context,
    log: EventLog,
    on_open_match: Optional[OpenMatchCallback] = None,
) -> None:
    """Fire the five phases for one trading day, in order.

    `on_open_match` (task 7) is invoked at the OPEN_MATCH phase with the
    pending orders accumulated by `Context.order_*`. The callback is
    expected to apply fills to the portfolio and to mark rejected orders.
    When `on_open_match` is `None`, OPEN_MATCH is a no-op (pre-task 7).
    """
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
            if on_open_match is not None:
                # Only consume when a matcher is wired in; otherwise pending
                # orders would silently vanish without any event.
                pending = context._consume_pending_orders()
                if pending:
                    on_open_match(today, pending)
            continue
        elif entry.phase is EventType.BAR_CLOSE:
            strategy.on_bar(context, view)
        elif entry.phase is EventType.AFTER_TRADING_END:
            strategy.after_trading_end(context)
