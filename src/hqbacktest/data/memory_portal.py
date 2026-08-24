"""In-memory data portal for tests, examples and offline smoke runs.

`InMemoryDataPortal` accepts domain types directly so unit tests can build
small, fully deterministic scenarios without touching the network, the local
filesystem, or `hqdata`. The portal mirrors `MarketDataPortal`'s contract and
performs the same validation (date format, sorted, unique, OHLC legality).
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from ..domain.bar import Bar
from ..domain.enums import EventType
from .errors import InvalidDataError, MissingDataError
from .portal import DataVersion, MarketDataPortal
from .validators import (
    assert_unique_sorted,
    validate_symbol,
    validate_yyyymmdd,
)


def _sorted_dates(dates: Iterable[str]) -> List[str]:
    materialized = list(dates)
    for value in materialized:
        validate_yyyymmdd(value, name="calendar date")
    if len(set(materialized)) != len(materialized):
        raise InvalidDataError("calendar", "duplicate dates")
    return sorted(materialized)


@dataclass
class InMemoryDataPortal(MarketDataPortal):
    """Pure-Python portal. No I/O of any kind."""

    source: str = "memory"
    as_of: str = "20991231"
    calendar: List[str] = field(default_factory=list)
    universe_by_date: Dict[str, List[str]] = field(default_factory=dict)
    bars_by_symbol: Dict[str, List[Bar]] = field(default_factory=dict)
    factor_by_symbol: Dict[str, List[Tuple[str, Decimal]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_yyyymmdd(self.as_of, name="as_of")
        if not isinstance(self.source, str) or not self.source:
            raise InvalidDataError("source", "must be a non-empty string")
        self.calendar = _sorted_dates(self.calendar)
        # Validate every bar and factor row up front.
        for sym, bars in self.bars_by_symbol.items():
            validate_symbol(sym)
            if any(not isinstance(bar, Bar) for bar in bars):
                raise InvalidDataError("bars", "rows must be Bar instances")
            if any(bar.symbol != sym for bar in bars):
                raise InvalidDataError("bars", f"symbol key {sym!r} does not match row")
            self._validate_bars_sorted_unique(bars)
            self._validate_bar_calendar_coverage(bars)
        for sym, rows in self.factor_by_symbol.items():
            validate_symbol(sym)
            self._validate_factor_sorted(rows)
            self._validate_factor_rows(rows)
        for date, symbols in self.universe_by_date.items():
            validate_yyyymmdd(date, name="universe date")
            for sym in symbols:
                validate_symbol(sym)
            if len(set(symbols)) != len(symbols):
                raise InvalidDataError("universe", f"duplicate symbols on {date}")

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_bars_sorted_unique(bars: Sequence[Bar]) -> None:
        dates = [b.date for b in bars]
        if len(set(dates)) != len(dates):
            raise InvalidDataError("bars", "duplicate dates")
        if dates != sorted(dates):
            raise InvalidDataError("bars", "must be sorted ascending by date")

    @staticmethod
    def _validate_factor_sorted(rows: Sequence[Tuple[str, Decimal]]) -> None:
        if not rows:
            return
        dates = [d for d, _ in rows]
        assert_unique_sorted(dates, name="factor dates")

    @staticmethod
    def _validate_factor_rows(rows: Sequence[Tuple[str, Decimal]]) -> None:
        for row in rows:
            if not isinstance(row, tuple) or len(row) != 2:
                raise InvalidDataError("factor", "rows must be (date, Decimal) tuples")
            date, factor = row
            validate_yyyymmdd(date, name="factor date")
            if not isinstance(factor, Decimal) or factor <= 0:
                raise InvalidDataError(
                    "factor", f"must be positive Decimal, got {factor!r}"
                )

    def _validate_bar_calendar_coverage(self, bars: Sequence[Bar]) -> None:
        if not self.calendar:
            return
        calendar = self.calendar_set()
        non_trading = [bar.date for bar in bars if bar.date not in calendar]
        if non_trading:
            raise InvalidDataError(
                "bar.date",
                f"dates are not in the configured trading calendar: {non_trading[:5]}",
            )

    def _bars_in_window(self, symbol: str, start: str, end: str) -> List[Bar]:
        validate_symbol(symbol)
        validate_yyyymmdd(start, name="start")
        validate_yyyymmdd(end, name="end")
        if start > end:
            raise InvalidDataError("window", f"start {start} > end {end}")
        all_bars = self.bars_by_symbol.get(symbol, [])
        dates = [b.date for b in all_bars]
        idx_start = bisect_left(dates, start)
        idx_end = bisect_right(dates, end)
        return all_bars[idx_start:idx_end]

    def _factors_in_window(
        self, symbol: str, start: str, end: str
    ) -> List[Tuple[str, Decimal]]:
        validate_symbol(symbol)
        validate_yyyymmdd(start, name="start")
        validate_yyyymmdd(end, name="end")
        if start > end:
            raise InvalidDataError("window", f"start {start} > end {end}")
        all_rows = self.factor_by_symbol.get(symbol, [])
        dates = [d for d, _ in all_rows]
        idx_start = bisect_left(dates, start)
        idx_end = bisect_right(dates, end)
        return all_rows[idx_start:idx_end]

    # ------------------------------------------------------------------ #
    # MarketDataPortal implementation
    # ------------------------------------------------------------------ #

    def data_version(self) -> DataVersion:
        return DataVersion(source=self.source, as_of=self.as_of)

    def get_calendar(self, start: str, end: str) -> List[str]:
        validate_yyyymmdd(start, name="start")
        validate_yyyymmdd(end, name="end")
        if start > end:
            raise InvalidDataError("window", f"start {start} > end {end}")
        idx_start = bisect_left(self.calendar, start)
        idx_end = bisect_right(self.calendar, end)
        return list(self.calendar[idx_start:idx_end])

    def is_trading_day(self, date: str) -> bool:
        validate_yyyymmdd(date)
        return date in self.calendar_set()

    def previous_trading_day(self, date: str) -> str:
        validate_yyyymmdd(date)
        idx = bisect_left(self.calendar, date) - 1
        if idx < 0:
            raise MissingDataError("previous trading day", f"no day before {date}")
        return self.calendar[idx]

    def next_trading_day(self, date: str) -> str:
        validate_yyyymmdd(date)
        idx = bisect_right(self.calendar, date)
        if idx >= len(self.calendar):
            raise MissingDataError("next trading day", f"no day after {date}")
        return self.calendar[idx]

    def get_universe(self, date: str) -> List[str]:
        validate_yyyymmdd(date)
        if date not in self.universe_by_date:
            # Convention: walk back to the most recent universe snapshot <= date.
            for snapshot_date in sorted(self.universe_by_date.keys(), reverse=True):
                if snapshot_date <= date:
                    return sorted(self.universe_by_date[snapshot_date])
            raise MissingDataError("universe", f"no universe on or before {date}")
        return sorted(self.universe_by_date[date])

    def get_bars(self, symbol: str, start: str, end: str) -> List[Bar]:
        bars = self._bars_in_window(symbol, start, end)
        if not bars:
            raise MissingDataError("bars", f"no bars for {symbol} in [{start}, {end}]")
        return list(bars)

    def get_factor(
        self, symbol: str, start: str, end: str
    ) -> List[Tuple[str, Decimal]]:
        rows = self._factors_in_window(symbol, start, end)
        if not rows:
            raise MissingDataError(
                "factor", f"no factor for {symbol} in [{start}, {end}]"
            )
        return list(rows)

    def calendar_set(self) -> set:
        return set(self.calendar)

    # ------------------------------------------------------------------ #
    # Mutators for tests
    # ------------------------------------------------------------------ #

    def add_bar(self, bar: Bar) -> None:
        if not isinstance(bar, Bar):
            raise InvalidDataError("bar", "must be a Bar instance")
        validate_symbol(bar.symbol)
        validate_yyyymmdd(bar.date, name="bar.date")
        # Trading-day coverage: once the calendar is populated, every bar
        # must land on a trading day (contract §3.1 "交易日覆盖范围").
        self._validate_bar_calendar_coverage([bar])
        bars = self.bars_by_symbol.get(bar.symbol, [])
        candidate = [*bars, bar]
        self._validate_bars_sorted_unique(candidate)
        self.bars_by_symbol[bar.symbol] = candidate

    def add_factor(self, symbol: str, date: str, factor: Decimal) -> None:
        validate_symbol(symbol)
        validate_yyyymmdd(date)
        if not isinstance(factor, Decimal) or factor <= 0:
            raise InvalidDataError("factor", f"must be positive Decimal, got {factor}")
        if self.calendar and date not in self.calendar_set():
            raise InvalidDataError(
                "factor.date", f"{date} is not in the configured trading calendar"
            )
        rows = self.factor_by_symbol.get(symbol, [])
        candidate = [*rows, (date, factor)]
        self._validate_factor_sorted(candidate)
        self._validate_factor_rows(candidate)
        self.factor_by_symbol[symbol] = candidate

    def add_calendar_dates(self, dates: Iterable[str]) -> None:
        new = _sorted_dates(dates)
        self.calendar = _sorted_dates(list(self.calendar) + list(new))
