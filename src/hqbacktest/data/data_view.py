"""DataView: read-only, visibility-aware data facade for strategies.

Strategies must read market data through a `DataView`, never through the raw
portal. The view enforces contract rule 13 (`visible_through` is a hard cap on
what the strategy can see) and exposes a small ergonomic API:

    view.history(symbol, field, bar_count) -> list of values
    view.current_price(symbol) -> latest close <= visible_through
    view.universe() -> list[str]  (as of visible_through)
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from .errors import FutureDataAccessError, MissingDataError
from .portal import MarketDataPortal
from .validators import validate_symbol, validate_yyyymmdd

VALID_FIELDS = ("open", "high", "low", "close", "volume")
DEFAULT_HISTORY_START = "19000101"


@dataclass
class DataView:
    """Visibility-aware data facade.

    `universe_start` is the earliest date the strategy is allowed to query.
    Together with `visible_through`, it forms the half-open window
    `[universe_start, visible_through]` that all reads are restricted to.
    """

    portal: MarketDataPortal
    visible_through: str
    universe_start: Optional[str] = None

    def __post_init__(self) -> None:
        validate_yyyymmdd(self.visible_through, name="visible_through")
        if self.universe_start is not None:
            validate_yyyymmdd(self.universe_start, name="universe_start")
            if self.universe_start > self.visible_through:
                raise FutureDataAccessError(self.universe_start, self.visible_through)

    def _guard(self, requested: str) -> None:
        if requested > self.visible_through:
            raise FutureDataAccessError(requested, self.visible_through)
        if self.universe_start is not None and requested < self.universe_start:
            raise FutureDataAccessError(requested, self.visible_through)

    # ------------------------------------------------------------------ #
    # Pass-through (still guarded)
    # ------------------------------------------------------------------ #

    def get_bars(self, symbol: str, start: str, end: str):
        self._guard(start)
        self._guard(end)
        return self.portal.get_bars(symbol, start, end)

    def get_factor(self, symbol: str, start: str, end: str):
        self._guard(start)
        self._guard(end)
        return self.portal.get_factor(symbol, start, end)

    def get_universe(self, date: Optional[str] = None) -> List[str]:
        target = self.visible_through if date is None else date
        self._guard(target)
        return self.portal.get_universe(target)

    def universe(self) -> List[str]:
        """Historical universe as of `visible_through`."""
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
        """
        validate_symbol(symbol)
        if field not in VALID_FIELDS:
            raise ValueError(f"field must be one of {VALID_FIELDS}, got {field!r}")
        if bar_count <= 0:
            raise ValueError(f"bar_count must be positive, got {bar_count}")
        start = (
            self.universe_start
            if self.universe_start is not None
            else DEFAULT_HISTORY_START
        )
        bars = self.portal.get_bars(symbol, start, self.visible_through)
        if len(bars) > bar_count:
            bars = bars[-bar_count:]
        return [getattr(b, field) for b in bars]

    def current_price(self, symbol: str) -> Optional[Decimal]:
        """Return the close price on or before `visible_through`, or None."""
        validate_symbol(symbol)
        start = (
            self.universe_start
            if self.universe_start is not None
            else DEFAULT_HISTORY_START
        )
        try:
            bars = self.portal.get_bars(symbol, start, self.visible_through)
        except MissingDataError:
            return None
        if not bars:
            return None
        return bars[-1].close
