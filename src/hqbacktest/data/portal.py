"""MarketDataPortal: the engine-facing data interface.

This module defines the protocol plus the small `DataVersion` payload. The
protocol is intentionally SDK-agnostic: no pandas DataFrame, no hqdata types,
no Tushare symbols leak into the engine.

Two concrete implementations live next to this module:
- `InMemoryDataPortal` for tests, examples, and the integration smoke test;
- `HqDataCsvPortal` for production, which reads hqdata CLI CSV snapshots.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Protocol, Tuple


@dataclass(frozen=True)
class DataVersion:
    """Identification of the data snapshot backing a portal."""

    source: str
    as_of: str  # YYYYMMDD
    schema_version: str = "v0.1"


class MarketDataPortal(Protocol):
    """Abstract data source.

    All date arguments are `YYYYMMDD` strings and inclusive on both ends.
    Returned dates/bars are sorted ascending and deduplicated. Implementations
    must validate inputs and raise `InvalidDataError` on bad inputs.
    """

    def data_version(self) -> DataVersion: ...

    def get_calendar(self, start: str, end: str) -> List[str]:
        """Trading days in [start, end] inclusive."""
        ...

    def is_trading_day(self, date: str) -> bool: ...

    def previous_trading_day(self, date: str) -> str:
        """Return the trading day strictly before `date`, or raise."""
        ...

    def next_trading_day(self, date: str) -> str:
        """Return the trading day strictly after `date`, or raise."""
        ...

    def get_universe(self, date: str, include_bj: bool = False) -> List[str]:
        """Historical stock list as of `date` (per contract §3.1).

        `include_bj=False` (the default) excludes Beijing Stock Exchange
        (`.BJ`) symbols, which are not supported in v0.1 (no first-day
        limit-up/down rule, distinct trading calendar, etc.). Pass
        `include_bj=True` to opt in; the engine still treats `.BJ` symbols
        as ordinary stocks from a data-layer perspective, but the broker
        and rule set do not yet enforce BSE-specific rules.
        """
        ...

    def get_bars(self, symbol: str, start: str, end: str) -> List["Bar"]:
        """Daily bars for `symbol` in [start, end] inclusive, ascending by date."""
        ...

    def get_factor(
        self, symbol: str, start: str, end: str
    ) -> List[Tuple[str, Decimal]]:
        """(date, factor) tuples for `symbol` in [start, end], ascending."""
        ...
