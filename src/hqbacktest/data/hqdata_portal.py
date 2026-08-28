"""HqDataCsvPortal: production portal backed by hqdata API.

After the v0.1 refactor, this portal no longer parses CSV files
directly. It delegates column mapping, schema validation, and
"file exists?" checks to `hqdata` via the unified `hqdata.api`
interface — specifically the `CsvSource` reading the
`~/.hqdata/{source}/...` snapshot layout that the `hqdata` CLI writes.

The portal still owns:

- **Path resolution.** Given a `source` reference (bare name like
  ``"tushare"`` or absolute path), resolve it to a `(data_root,
  source_name)` pair and pin the right `hqdata` snapshot root via
  ``hqdata.init_source("csv", root=..., source_name=...)``.

- **DataFrame -> domain conversion.** `hqdata` returns
  `pandas.DataFrame` with float64 prices; hqbacktest's `Bar` and
  factor require `Decimal`. The conversion lives in
  ``hqbacktest.data._converters`` and is applied row-by-row.

- **Caching.** Per-run double-layer cache:

    - ``_daily_index[date] = {symbol: Bar}`` keeps each daily snapshot
      parsed at most once. Hits in the second layer avoid re-reading
      the same CSV (well, same CSV via hqdata) within a run.
    - ``_factor_index[date] = {symbol: Decimal}`` mirrors the layout for
      factor files.
    - ``_symbol_bars[symbol] = [Bar, ...]`` and
      ``_symbol_factors[symbol] = [(date, Decimal), ...]`` are
      cumulative views; ``get_bars`` / ``get_factor`` slice them with
      `bisect`, so per-call cost is O(log N).

- **Exception translation.** `hqdata.SnapshotFileMissingError` is
  re-raised as ``hqbacktest.errors.SnapshotFileMissingError`` so the
  rest of the engine keeps catching the existing class.
"""

from bisect import bisect_left, bisect_right
from datetime import date as _date
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import os

import pandas as pd

import hqdata  # type: ignore[import-not-found]  # noqa: F401  injected by editable install
from hqdata.errors import SnapshotFileMissingError as _HqdataSnapshotError

from ..domain.bar import Bar
from ._converters import value_to_factor
from .cache import CacheKey, DataCache
from .errors import (
    InvalidDataError,
    MissingDataError,
    SnapshotFileMissingError,
)
from .portal import DataVersion, MarketDataPortal
from .validators import (
    assert_unique_sorted,
    validate_symbol,
    validate_yyyymmdd,
)

DEFAULT_DATA_ROOT = "~/.hqdata"

# Read the entire calendar file once and cache locally — querying by
# `get_calendar(start, end)` would silently drop rows outside the window
# (including dates with values that happen to be invalid YYYYMMDD strings),
# which the portal needs to surface as `InvalidDataError`. We use the
# widest sane YYYYMMDD range (so hqdata accepts the query without filtering)
# and validate every row ourselves.
_CALENDAR_FETCH_START = "00000000"
_CALENDAR_FETCH_END = "99999999"


def resolve_source_location(
    source: str, default_data_root: str = DEFAULT_DATA_ROOT
) -> Tuple[str, str]:
    """Map a source reference to `(data_root, source_name)`.

    `source` may be either:

    * a bare directory name (e.g. ``"tushare"``). It is paired with
      ``default_data_root`` (the ``[data].data_root`` config, default
      ``~/.hqdata``).
    * an absolute path to the snapshot directory itself, with ``~``
      expansion (e.g. ``"~/.hqdata/tushare"`` or
      ``"/home/<user>/.hqdata/tushare"``). It is split into
      ``(parent_dir, basename)``.

    Relative paths that mix both forms (``"foo/bar"``) are rejected:
    cwd-relative resolution is ambiguous for backtests and is never
    relied on. Empty strings, ``.``, ``..``, and the filesystem root
    are also rejected.
    """
    if not isinstance(source, str) or not source:
        raise InvalidDataError("source", "must be a non-empty string")
    if source in (".", "..", "/"):
        raise InvalidDataError(
            "source",
            f"must be a directory name or absolute path; got {source!r}",
        )
    expanded = os.path.expanduser(source)
    p = Path(expanded)
    if p.is_absolute():
        if p.name in ("", ".", ".."):
            raise InvalidDataError(
                "source",
                "absolute path {!r} resolves to invalid directory "
                "name {!r}".format(source, p.name),
            )
        return (str(p.parent), p.name)
    if "/" in source or "\\" in source:
        raise InvalidDataError(
            "source",
            "must be a directory name or an absolute path; got "
            "relative path {!r} (configure its parent with data_root "
            "or pass an absolute path instead)".format(source),
        )
    return (default_data_root, source)


class HqDataCsvPortal(MarketDataPortal):
    """Read-only portal backed by an hqdata CSV snapshot.

    Construction always routes through ``hqdata.init_source("csv", ...)``
    so the underlying `CsvSource` is pointed at the resolved snapshot
    directory. The portal makes no direct CSV / pandas calls of its own
    — all data access goes through `hqdata.api`.
    """

    def __init__(
        self,
        source: str,
        data_root: Optional[str] = None,
    ) -> None:
        if not isinstance(source, str) or not source:
            raise InvalidDataError("source", "must be a non-empty string")
        env_root = data_root if data_root is not None else DEFAULT_DATA_ROOT
        resolved_root, resolved_name = resolve_source_location(source, env_root)
        self._data_root: str = str(Path(resolved_root).expanduser().resolve())
        self._source_name: str = resolved_name
        self._source_label: str = source
        self._root_path: Path = Path(self._data_root) / self._source_name

        # hqdata's CsvSource carries the snapshot layout; point it at
        # the resolved path. Construction is cheap — file existence is
        # only enforced on first access.
        hqdata.init_source(
            "csv",
            root=str(self._root_path),
            source_name=self._source_name,
        )

        self._cache = DataCache()
        # Per-run caches.
        self._daily_index: Dict[str, Dict[str, Bar]] = {}
        self._factor_index: Dict[str, Dict[str, Decimal]] = {}
        self._symbol_bars: Dict[str, List[Bar]] = {}
        self._symbol_factors: Dict[str, List[Tuple[str, Decimal]]] = {}
        self._calendar: Optional[List[Tuple[str, str]]] = None
        self._universe_cache: Dict[str, List[str]] = {}

        self._data_version = DataVersion(
            source=self._source_label,
            as_of=self._resolve_as_of(),
        )

    # ------------------------------------------------------------------ #
    # Identity
    # ------------------------------------------------------------------ #

    def data_version(self) -> DataVersion:
        return self._data_version

    def source(self) -> str:
        return self._source_label

    def source_name(self) -> str:
        return self._source_name

    def data_root(self) -> Path:
        return Path(self._data_root)

    def snapshot_root(self) -> Path:
        return self._root_path

    def cache(self) -> DataCache:
        return self._cache

    def _resolve_as_of(self) -> str:
        """Pick the snapshot's `as_of` from the calendar.

        `MissingDataError` (no calendar.csv at all) is treated as "snapshot
        empty": fall back to today's date so the engine can still publish a
        `data_version`. `InvalidDataError` (calendar exists but is corrupt)
        is a real data infrastructure failure and MUST propagate; silently
        masking it with `_date.today()` would let the engine believe the
        snapshot is healthy when it is not.
        """
        try:
            calendar = self._read_calendar()
        except MissingDataError:
            return _date.today().strftime("%Y%m%d")
        opens = [d for d, is_open in calendar if is_open == "Y"]
        if opens:
            return opens[-1]
        return _date.today().strftime("%Y%m%d")

    # ------------------------------------------------------------------ #
    # Calendar
    # ------------------------------------------------------------------ #

    def _read_calendar(self) -> List[Tuple[str, str]]:
        """Load the entire calendar via hqdata, cached for the run.

        Returns a list of `(date, is_open)` tuples (where is_open is
        ``"Y"`` / ``"N"``) sorted ascending by date.

        Raises `MissingDataError` when the calendar is **truly**
        unavailable — i.e. when even hqdata's empty-result fallback
        returns an empty frame and the path cannot be located. Used by
        ``_resolve_as_of`` to gracefully fall back to "today".
        """
        if self._calendar is not None:
            return self._calendar
        try:
            df = hqdata.get_calendar(_CALENDAR_FETCH_START, _CALENDAR_FETCH_END)
        except _HqdataSnapshotError as exc:
            raise MissingDataError(
                "calendar",
                f"calendar.csv not found: {exc.path}",
            ) from exc
        if df.empty:
            raise MissingDataError(
                "calendar",
                "no calendar rows available (calendar.csv missing or empty)",
            )
        dates = [validate_yyyymmdd(v) for v in df["date"].tolist()]
        assert_unique_sorted(dates, name="calendar dates")
        flags = [str(v).strip().upper() for v in df["is_open"].tolist()]
        for flag in flags:
            if flag not in ("Y", "N"):
                raise InvalidDataError(
                    "calendar.is_open", f"expected Y or N, got {flag!r}"
                )
        self._calendar = list(zip(dates, flags))
        return self._calendar

    def get_calendar(self, start: str, end: str) -> List[str]:
        validate_yyyymmdd(start, name="start")
        validate_yyyymmdd(end, name="end")
        if start > end:
            raise InvalidDataError("window", f"start {start} > end {end}")
        cache_key = CacheKey(
            self._data_root, self._source_name, "calendar", "", "", start, end
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)
        calendar = self._read_calendar()
        result = [d for d, flag in calendar if flag == "Y" and start <= d <= end]
        self._cache.put(cache_key, list(result))
        return list(result)

    def is_trading_day(self, d: str) -> bool:
        validate_yyyymmdd(d)
        cache_key = CacheKey(
            self._data_root, self._source_name, "is_trading_day", "", "", d, d
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        calendar = self._read_calendar()
        result = any(date == d and flag == "Y" for date, flag in calendar)
        self._cache.put(cache_key, result)
        return result

    def previous_trading_day(self, d: str) -> str:
        validate_yyyymmdd(d)
        calendar = [
            date for date, flag in self._read_calendar() if flag == "Y" and date < d
        ]
        if not calendar:
            raise MissingDataError("previous trading day", f"no day before {d}")
        return calendar[-1]

    def next_trading_day(self, d: str) -> str:
        validate_yyyymmdd(d)
        calendar = [
            date for date, flag in self._read_calendar() if flag == "Y" and date > d
        ]
        if not calendar:
            raise MissingDataError("next trading day", f"no day after {d}")
        return calendar[0]

    # ------------------------------------------------------------------ #
    # Universe
    # ------------------------------------------------------------------ #

    def get_universe(self, date: str, include_bj: bool = False) -> List[str]:
        """Return the historical stock list as of `date`.

        `.BJ` (Beijing Stock Exchange) symbols are excluded by default
        since v0.1 does not yet support BSE-specific trading rules
        (no first-day limit-up/down, distinct trading calendar, etc.).
        Pass `include_bj=True` to opt in.
        """
        validate_yyyymmdd(date)
        cache_key = CacheKey(
            self._data_root, self._source_name, "universe", "", "", date, date
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            full = list(cached)
        else:
            try:
                df = hqdata.get_stock_list(trade_date=date)
            except _HqdataSnapshotError as exc:
                raise SnapshotFileMissingError("stock_list", str(exc.path)) from exc
            if df.empty:
                raise MissingDataError("universe", f"empty universe on {date}")
            symbols = sorted(set(df["symbol"].tolist()))
            self._cache.put(cache_key, list(symbols))
            full = list(symbols)
        if include_bj:
            return full
        return [sym for sym in full if not sym.endswith(".BJ")]

    # ------------------------------------------------------------------ #
    # Bars
    # ------------------------------------------------------------------ #

    def get_bars(self, symbol: str, start: str, end: str) -> List[Bar]:
        """Return bars for `symbol` in [start, end], **allowing per-day gaps**.

        Per-suspension / pre-IPO / post-delisting semantics:
            - Days in the window with no row for `symbol` are simply
              absent from the result.
            - An empty result is a legitimate business outcome (the
              symbol did not trade at all in this window); it is
              **not** an error.
            - Whole-day snapshot files missing on disk raise
              `SnapshotFileMissingError` so the engine aborts with a
              clear `DATA_ERROR`.

        Performance:
            - Per-symbol cumulative list sliced by `bisect`: O(log N).
            - Each daily snapshot is parsed (via hqdata) at most once
              per run.
        """
        validate_symbol(symbol)
        validate_yyyymmdd(start, name="start")
        validate_yyyymmdd(end, name="end")
        if start > end:
            raise InvalidDataError("window", f"start {start} > end {end}")
        self._ensure_symbol_bars(symbol, start, end)
        cumulative = self._symbol_bars[symbol]
        dates = [b.date for b in cumulative]
        idx_start = bisect_left(dates, start)
        idx_end = bisect_right(dates, end)
        return list(cumulative[idx_start:idx_end])

    def _ensure_symbol_bars(self, symbol: str, start: str, end: str) -> None:
        cumulative = self._symbol_bars.get(symbol)
        if cumulative and cumulative[0].date <= start and cumulative[-1].date >= end:
            return
        if not cumulative:
            self._extend_symbol_bars(symbol, start, end)
            return
        if end > cumulative[-1].date:
            self._extend_symbol_bars(symbol, cumulative[-1].date, end)
        if start < cumulative[0].date:
            self._extend_symbol_bars(symbol, start, cumulative[0].date)

    def _extend_symbol_bars(self, symbol: str, start: str, end: str) -> None:
        existing = self._symbol_bars.get(symbol, [])
        have = {b.date for b in existing}
        calendar = self.get_calendar(start, end)
        for trading_day in calendar:
            if trading_day in have:
                continue
            bar = self._read_single_bar(symbol, trading_day)
            if bar is not None:
                existing.append(bar)
        existing.sort(key=lambda b: b.date)
        self._symbol_bars[symbol] = existing

    def _read_single_bar(self, symbol: str, date: str) -> Optional[Bar]:
        """Return the bar for one (symbol, day), populated from hqdata.

        Returns None for a per-symbol gap (suspended / pre-IPO /
        delisted). Raises `SnapshotFileMissingError` when the day's
        snapshot file is missing on disk — the engine converts that
        into a `DATA_ERROR` abort, distinct from a quiet gap.
        """
        per_day = self._daily_index.get(date)
        if per_day is None:
            per_day = self._read_day_bars(date)
            self._daily_index[date] = per_day
        return per_day.get(symbol)

    def _read_day_bars(self, date: str) -> Dict[str, Bar]:
        """Fetch all bar rows for `date` via hqdata, convert to Bar map.

        Pulls the entire day's DataFrame in one hqdata call
        (symbol=None -> no symbol filter) and converts row-by-row.
        The full-day fetch is intentional: hqbacktest's per-symbol
        queries share the same daily DataFrame, so paying one parse
        per day is cheaper than one query per (symbol, day).
        """
        try:
            df = hqdata.get_stock_daily_bar(symbol=None, start_date=date, end_date=date)
        except _HqdataSnapshotError as exc:
            raise SnapshotFileMissingError("stock_daily", str(exc.path)) from exc
        if df.empty:
            return {}
        result: Dict[str, Bar] = {}
        for row in df.itertuples(index=False):
            sym = getattr(row, "symbol")
            try:
                # Cast float64 cells to `str` before handing to Bar.from_raw
                # — contract §3.2 forbids `Decimal(float(...))` because it
                # inherits binary-float artifacts. `Bar.from_raw` rejects
                # floats, so going through `str` keeps precision clean.
                bar = Bar.from_raw(
                    symbol=sym,
                    date=getattr(row, "date"),
                    open=str(getattr(row, "open")),
                    high=str(getattr(row, "high")),
                    low=str(getattr(row, "low")),
                    close=str(getattr(row, "close")),
                    volume=int(getattr(row, "volume")),
                )
            except (ValueError, TypeError) as exc:
                raise InvalidDataError(
                    "stock_daily",
                    f"{date}: malformed bar for {sym}: {exc}",
                ) from exc
            result[sym] = bar
        return result

    # ------------------------------------------------------------------ #
    # Factor
    # ------------------------------------------------------------------ #

    def get_factor(
        self, symbol: str, start: str, end: str
    ) -> List[Tuple[str, Decimal]]:
        """Return (date, factor) tuples for `symbol` in [start, end].

        Same gap semantics as `get_bars`: a per-symbol absence on a
        trading day omits that day from the result, while a missing
        whole-day factor file raises `SnapshotFileMissingError`.
        """
        validate_symbol(symbol)
        validate_yyyymmdd(start, name="start")
        validate_yyyymmdd(end, name="end")
        if start > end:
            raise InvalidDataError("window", f"start {start} > end {end}")
        self._ensure_symbol_factors(symbol, start, end)
        cumulative = self._symbol_factors[symbol]
        dates = [d for d, _ in cumulative]
        idx_start = bisect_left(dates, start)
        idx_end = bisect_right(dates, end)
        return list(cumulative[idx_start:idx_end])

    def _ensure_symbol_factors(self, symbol: str, start: str, end: str) -> None:
        cumulative = self._symbol_factors.get(symbol)
        if cumulative and cumulative[0][0] <= start and cumulative[-1][0] >= end:
            return
        if not cumulative:
            self._extend_symbol_factors(symbol, start, end)
            return
        if end > cumulative[-1][0]:
            self._extend_symbol_factors(symbol, cumulative[-1][0], end)
        if start < cumulative[0][0]:
            self._extend_symbol_factors(symbol, start, cumulative[0][0])

    def _extend_symbol_factors(self, symbol: str, start: str, end: str) -> None:
        existing = self._symbol_factors.get(symbol, [])
        have = {d for d, _ in existing}
        calendar = self.get_calendar(start, end)
        for trading_day in calendar:
            if trading_day in have:
                continue
            factor = self._read_single_factor(symbol, trading_day)
            if factor is not None:
                existing.append((trading_day, factor))
        existing.sort(key=lambda x: x[0])
        self._symbol_factors[symbol] = existing

    def _read_single_factor(self, symbol: str, date: str) -> Optional[Decimal]:
        """Return the factor for one (symbol, day) via hqdata."""
        per_day = self._factor_index.get(date)
        if per_day is None:
            per_day = self._read_day_factors(date)
            self._factor_index[date] = per_day
        return per_day.get(symbol)

    def _read_day_factors(self, date: str) -> Dict[str, Decimal]:
        """Fetch the entire day's factors via hqdata, convert to a map.

        hqdata's `get_stock_factor(trade_date, symbol=None)` returns an
        empty frame (it does not auto-resolve the universe), so we
        fetch the day's stock list first and pass the symbol CSV in
        one call — keeps the round-trip to exactly two hqdata calls
        per factor day regardless of universe size.
        """
        try:
            stock_list_df = hqdata.get_stock_list(trade_date=date)
        except _HqdataSnapshotError as exc:
            raise SnapshotFileMissingError("stock_list", str(exc.path)) from exc
        symbol_csv: Optional[str] = None
        if not stock_list_df.empty:
            symbol_csv = ",".join(stock_list_df["symbol"].tolist())
        if not symbol_csv:
            return {}
        try:
            df = hqdata.get_stock_factor(trade_date=date, symbol=symbol_csv)
        except _HqdataSnapshotError as exc:
            raise SnapshotFileMissingError("stock_factor", str(exc.path)) from exc
        if df.empty:
            return {}
        result: Dict[str, Decimal] = {}
        for row in df.itertuples(index=False):
            sym = getattr(row, "symbol")
            d = getattr(row, "date")
            try:
                result[sym] = value_to_factor(
                    getattr(row, "factor"), symbol=sym, date=str(d)
                )
            except InvalidDataError as exc:
                raise InvalidDataError(
                    "stock_factor",
                    f"{date}: {exc.detail}",
                ) from exc
        return result

    # ------------------------------------------------------------------ #
    # Internals: not part of MarketDataPortal
    # ------------------------------------------------------------------ #


# Re-export for legacy imports; keep backward compatibility.
__all__ = ["HqDataCsvPortal", "resolve_source_location", "DEFAULT_DATA_ROOT"]
