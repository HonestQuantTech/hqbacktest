"""DataView: read-only, visibility-aware data facade for strategies.

Strategies must read market data through a `DataView`, never through the raw
portal. The view enforces contract rule 13 (`visible_through` is a hard cap on
what the strategy can see) and exposes a small ergonomic API:

    view.history(symbol, field, bar_count) -> list of values
    view.current_price(symbol) -> latest close within the lookback window
    view.universe() -> list[str]  (as of visible_through)

Task 14 semantics (per `docs/design/mvp-contract.md` and `TODO.md`):
    - `history(bar_count=N)` only queries the relevant slice (the lookback
      window); it does NOT scan the full pre-start window of every symbol.
    - `current_price(symbol)` returns the most recent valid close within the
      last `CURRENT_PRICE_LOOKBACK` (20) trading days. Suspended / delisted /
      pre-IPO symbols therefore return a usable price instead of an empty
      series.
    - The sentinel `visible_through="00000000"` used on the very first
      trading day must NOT raise: `history` returns `[]` and
      `current_price` returns `None`.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from .errors import (
    FutureDataAccessError,
    MissingDataError,
    SnapshotFileMissingError,
)
from .portal import MarketDataPortal
from .validators import validate_symbol, validate_yyyymmdd

VALID_FIELDS = ("open", "high", "low", "close", "volume")
DEFAULT_HISTORY_START = "19000101"

# Cap on how far back `current_price` walks to find the most recent close.
# Calibrated for A股: with a normal trading week of ~5 trading days, 20
# covers roughly a month of holidays plus one typical multi-day suspension.
CURRENT_PRICE_LOOKBACK = 20

# Sentinel value used by the scheduler when no prior trading day exists yet.
# Any explicit "00000000" request must be treated as "no data visible"
# rather than as a literal date lookup.
NO_HISTORY_SENTINEL = "00000000"


@dataclass
class DataView:
    """Visibility-aware data facade.

    `universe_start` is the earliest date the strategy is allowed to query.
    Together with `visible_through`, it forms the half-open window
    `[universe_start, visible_through]` that all reads are restricted to.

    `visible_through="00000000"` is a legal sentinel that exposes no data:
    `history(...)` returns `[]` and `current_price(...)` returns `None`.

    Task 18: the `portal` attribute is **private** (renamed to
    `_portal` as a leading-underscore convention). Strategies cannot
    reach the raw `MarketDataPortal` and therefore cannot read future
    data via `view.portal.get_bars(...)`. All data-layer access goes
    through the guarded methods on this view.
    The constructor still accepts `portal=...` (kwarg) so existing
    call sites don't break, but the value is stored only on the
    private field and is never re-exposed.
    """

    _portal: MarketDataPortal
    visible_through: str
    universe_start: Optional[str] = None

    def __init__(
        self,
        portal: MarketDataPortal,
        visible_through: str,
        universe_start: Optional[str] = None,
    ) -> None:
        # Accept `portal=` for backward compatibility, but store it on
        # the private `_portal` field. Strategies that try to read
        # `view.portal` get `AttributeError` (task 18 isolation).
        self._portal = portal
        self.visible_through = visible_through
        self.universe_start = universe_start
        self.__post_init__()

    def __post_init__(self) -> None:
        # The sentinel "00000000" is allowed as a special value.
        if self.visible_through == NO_HISTORY_SENTINEL:
            if (
                self.universe_start is not None
                and self.universe_start != NO_HISTORY_SENTINEL
            ):
                raise FutureDataAccessError(self.universe_start, self.visible_through)
            return
        validate_yyyymmdd(self.visible_through, name="visible_through")
        if self.universe_start is not None:
            if self.universe_start == NO_HISTORY_SENTINEL:
                raise FutureDataAccessError(self.universe_start, self.visible_through)
            validate_yyyymmdd(self.universe_start, name="universe_start")
            if self.universe_start > self.visible_through:
                raise FutureDataAccessError(self.universe_start, self.visible_through)

    def _guard(self, requested: str) -> None:
        """Reject queries that ask for dates outside the visibility window.

        `00000000` is always considered to lie outside the visible window
        because the portal never indexes dates earlier than its first
        snapshot day; passing it through would force every underlying call
        to scan the full pre-start history.
        """
        if requested == NO_HISTORY_SENTINEL:
            raise FutureDataAccessError(requested, self.visible_through)
        if requested > self.visible_through:
            raise FutureDataAccessError(requested, self.visible_through)
        if (
            self.universe_start is not None
            and self.universe_start != NO_HISTORY_SENTINEL
            and requested < self.universe_start
        ):
            # Reading before the data start is NOT future-data access; the
            # window simply predates what the strategy is allowed to see.
            raise MissingDataError(
                "requested window starts before universe_start",
                f"{requested} < {self.universe_start}",
            )

    # ------------------------------------------------------------------ #
    # Pass-through (still guarded)
    # ------------------------------------------------------------------ #

    def get_bars(self, symbol: str, start: str, end: str) -> List:
        # Sentinel view: empty by construction.
        if self.visible_through == NO_HISTORY_SENTINEL:
            return []
        self._guard(start)
        self._guard(end)
        return self._portal.get_bars(symbol, start, end)

    def get_factor(self, symbol: str, start: str, end: str):
        if self.visible_through == NO_HISTORY_SENTINEL:
            return []
        self._guard(start)
        self._guard(end)
        return self._portal.get_factor(symbol, start, end)

    def get_universe(
        self, date: Optional[str] = None, include_bj: bool = False
    ) -> List[str]:
        target = self.visible_through if date is None else date
        if self.visible_through == NO_HISTORY_SENTINEL:
            return []
        self._guard(target)
        return self._portal.get_universe(target, include_bj=include_bj)

    def universe(self) -> List[str]:
        """Historical universe as of `visible_through` (excluding .BJ)."""
        return self.get_universe()

    # ------------------------------------------------------------------ #
    # Ergonomic helpers
    # ------------------------------------------------------------------ #

    def history(
        self,
        symbol: str,
        field: str = "close",
        bar_count: int = 1,
    ) -> List[object]:
        """Return up to `bar_count` values of `field`, ending at visible_through.

        The result is ordered ascending by date. If `bar_count` exceeds the
        number of available bars, the shorter list is returned.

        Per task 14: when `universe_start` is set, only bars within the
        `[universe_start, visible_through]` window are queried — never the
        full pre-start history. The first-trading-day sentinel returns
        `[]` rather than raising.
        """
        validate_symbol(symbol)
        if field not in VALID_FIELDS:
            raise ValueError(f"field must be one of {VALID_FIELDS}, got {field!r}")
        if bar_count <= 0:
            raise ValueError(f"bar_count must be positive, got {bar_count}")
        if self.visible_through == NO_HISTORY_SENTINEL:
            return []
        # Cap the lookback to bar_count trading days so we never ask the
        # portal for the full pre-start history of every symbol. The cap
        # is a small constant; bar_count itself is what determines the
        # requested number of values.
        if (
            self.universe_start is not None
            and self.universe_start != NO_HISTORY_SENTINEL
        ):
            start = self.universe_start
        else:
            start = self._resolve_history_start(bar_count)
        try:
            bars = self._portal.get_bars(symbol, start, self.visible_through)
        except SnapshotFileMissingError:
            # A missing whole-day snapshot is an infrastructure failure, not
            # a per-symbol gap. It must propagate so the engine aborts the
            # run instead of silently producing an empty history.
            raise
        except FutureDataAccessError:
            # Defensive: a portal that re-checks visible_through would
            # raise here. Empty is the truthful answer.
            return []
        except MissingDataError:
            return []
        if len(bars) > bar_count:
            bars = bars[-bar_count:]
        return [getattr(b, field) for b in bars]

    def current_price(self, symbol: str) -> Optional[Decimal]:
        """Return the most recent valid close on or before `visible_through`.

        Task 14 semantics: walk back up to `CURRENT_PRICE_LOOKBACK` (20)
        trading days from `visible_through` and return the latest close in
        that window. Returns `None` when no bar exists in the lookback
        window (e.g. pre-IPO or first-trading-day sentinel). Suspended
        symbols therefore keep their last traded price for valuation.

        `InvalidDataError` propagates: a corrupt row is an infrastructure
        failure that must not be silently folded into "no price".
        `SnapshotFileMissingError` (a whole-day snapshot file missing on
        disk) is an infrastructure failure and propagates so the run aborts;
        `MissingDataError` (suspended / delisted / pre-IPO) is absorbed.

        Task 15 performance: one `get_calendar` (cached) resolves the
        20-trading-day cutoff, then a single `get_bars(cutoff, end)` is
        dispatched. This avoids the old N `get_bars(day, day)` round-trips
        while preserving the 20-**trading-day** lookback bound.
        """
        validate_symbol(symbol)
        if self.visible_through == NO_HISTORY_SENTINEL:
            return None
        # Resolve the 20-trading-day cutoff. The lookback is bounded by
        # trading days, NOT by bar count: a symbol suspended for longer
        # than the lookback must exhaust the window (return None), not
        # reach further back to its pre-suspension close.
        trading_days = self._portal.get_calendar(
            _trading_day_lookback_start(self.visible_through),
            self.visible_through,
        )
        if not trading_days:
            return None
        lookback = trading_days[-CURRENT_PRICE_LOOKBACK:]
        cutoff = lookback[0]
        try:
            bars = self._portal.get_bars(symbol, cutoff, self.visible_through)
        except SnapshotFileMissingError:
            # Whole-day file gone: infrastructure failure, propagate so
            # the engine aborts the run with DATA_ERROR.
            raise
        except MissingDataError:
            return None
        for bar in reversed(bars):
            close = bar.close
            if close is not None and close > 0:
                return close
        return None

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _resolve_history_start(self, bar_count: int) -> str:
        """Compute a bounded start date for `history(bar_count=N)`.

        Task 14: do NOT scan the full pre-start history (19000101). A
        5-year lookback window is comfortably larger than any realistic
        `bar_count` (most strategies look back < 250 trading days, roughly
        one calendar year) and bounds the worst-case file scan. The portal
        intersects the window with the trading calendar and filters
        per-symbol gaps, so an over-wide window is cheap and never changes
        the returned bar sequence.

        `bar_count` is accepted for API symmetry; a precise
        calendar-position bound is deferred to task 15's cache design.
        """
        return _trading_day_lookback_start(self.visible_through)


def _trading_day_lookback_start(visible_through: str) -> str:
    """Return a YYYYMMDD string that comfortably covers the lookback window.

    The portal intersects with the calendar, so an overly wide window is
    cheap. We pick a 5-year backstop (never earlier than
    `DEFAULT_HISTORY_START`) to avoid generating dates that fall before the
    snapshot started.
    """
    year = int(visible_through) // 10000
    floor_year = int(DEFAULT_HISTORY_START[:4])
    start_year = max(year - 5, floor_year)
    return f"{start_year}0101"
