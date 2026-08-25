"""Trading day iterator: only uses the portal's calendar (contract §6 rule 1).

Natural-day iteration is forbidden: the iterator must not invent dates that
are not in the calendar returned by the configured `MarketDataPortal`.
"""

from typing import Iterator, List

from ..data.portal import MarketDataPortal
from ..data.validators import validate_yyyymmdd
from .errors import ConfigurationError


class TradingDayIterator:
    """Yields trading days in `[start, end]` (inclusive), in ascending order.

    An empty trading-day window is a hard error. Silent success on a
    zero-day run produces misleading "no signals" reports and breaks
    reproducibility, so the iterator raises `ConfigurationError`
    instead of yielding an empty sequence.
    """

    def __init__(
        self,
        portal: MarketDataPortal,
        start: str,
        end: str,
    ) -> None:
        validate_yyyymmdd(start, name="start")
        validate_yyyymmdd(end, name="end")
        if start > end:
            raise ConfigurationError(f"start {start} is after end {end}")
        self._portal = portal
        self._start = start
        self._end = end
        self._trading_days: List[str] = list(portal.get_calendar(start, end))
        if not self._trading_days:
            # Use `source_name()` (CSV portal) or fall back to the
            # `source` attribute (memory portal). Both portals expose a
            # human-readable name so the error is informative.
            source_name = (
                portal.source_name()
                if hasattr(portal, "source_name")
                else getattr(portal, "source", "<unknown>")
            )
            raise ConfigurationError(
                f"no trading days in [{start}, {end}] for source "
                f"{source_name!r}; the backtest window has no data"
            )
        self._index = 0

    def __iter__(self) -> Iterator[str]:
        self._index = 0
        return self

    def __next__(self) -> str:
        if self._index >= len(self._trading_days):
            raise StopIteration
        day = self._trading_days[self._index]
        self._index += 1
        return day

    def __len__(self) -> int:
        return len(self._trading_days)

    def is_empty(self) -> bool:
        return len(self._trading_days) == 0
