"""HqDataCsvPortal: production portal backed by hqdata CSV snapshots.

Rules enforced here:
    - **No `import hqdata`**, no `hqdata.api`, no `hqdata.sources`, no SDK
      imports, no network access.
    - Reads only the CSV files dropped by the hqdata CLI at
      `{data_root}/{source}/...`:
          calendar.csv
          stock_list/{YYYYMMDD}.csv
          stock_daily/{YYYYMMDD}.csv
          stock_factor/{YYYYMMDD}.csv
        - `data_root` defaults to `~/.hqdata` and is overridable through the
            constructor argument.
        - `source` is the directory name under `data_root` (e.g. `tushare`).
    - `get_universe(date)` reads exactly `stock_list/{date}.csv`; missing
      snapshot raises `SnapshotFileMissingError` and never falls back to
      other dates.
    - Cache keys include the normalized `data_root` so two portals pointing
      at different roots cannot share entries.

Performance design:
    - Each `stock_daily/{D}.csv` is parsed at most once per run; the
      parsed result is cached as `_daily_index[date] = {symbol: Bar}`.
    - Each `stock_factor/{D}.csv` is parsed at most once per run; cached
      as `_factor_index[date] = {symbol: Decimal}`.
    - Per-symbol cumulative views (`_symbol_bars[symbol] = [Bar, ...]`,
      `_symbol_factors[symbol] = [(date, Decimal), ...]`) are derived
      lazily on first access and reused across all overlapping queries.
    - `get_bars` / `get_factor` slice the cumulative lists via `bisect`,
      so per-call cost is O(log N) regardless of the window.
    - `Bar` / factor objects are reused across overlapping queries; only
      the returned list is a defensive copy.
"""

from bisect import bisect_left, bisect_right
from datetime import date as _date
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import os

import pandas as pd

from ..domain.bar import Bar
from .cache import CacheKey, DataCache
from .errors import (
    InvalidDataError,
    MissingDataError,
    SnapshotFileMissingError,
    UnknownSymbolError,
)
from .portal import DataVersion, MarketDataPortal
from .validators import (
    assert_unique_sorted,
    require_columns,
    validate_symbol,
    validate_yyyymmdd,
)

DEFAULT_DATA_ROOT = "~/.hqdata"


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
    # Expand `~` so `~/.hqdata/tushare` is treated as an absolute path
    # on every platform. `expanduser` is a no-op for strings without `~`.
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
    # Bare name (no path separators). Pair with `default_data_root`.
    if "/" in source or "\\" in source:
        raise InvalidDataError(
            "source",
            "must be a directory name or an absolute path; got "
            "relative path {!r} (configure its parent with data_root "
            "or pass an absolute path instead)".format(source),
        )
    return (default_data_root, source)


class HqDataCsvPortal(MarketDataPortal):
    """Read-only CSV portal backed by a local hqdata snapshot directory.

    The portal never imports `hqdata` or any data source SDK. Path resolution
    is performed once in `__init__`; subsequent reads hit the local
    filesystem only.
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
        self._source_label: str = source  # the original string, for display
        self._root_path: Path = Path(self._data_root) / self._source_name
        self._cache = DataCache()
        # Per-day file caches (`{date: {symbol: Bar/factor}}`) and
        # per-symbol cumulative views. The per-day cache ensures each CSV
        # file is parsed at most once; the cumulative view makes
        # `get_bars`/`get_factor` O(log N) regardless of window.
        self._daily_index: Dict[str, Dict[str, Bar]] = {}
        self._factor_index: Dict[str, Dict[str, Decimal]] = {}
        self._symbol_bars: Dict[str, List[Bar]] = {}
        self._symbol_factors: Dict[str, List[Tuple[str, Decimal]]] = {}
        # `as_of` is the latest open trading day available on disk, falling
        # back to today's calendar date when the snapshot is empty.
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
        cache_key = CacheKey(
            self._data_root, self._source_name, "calendar_raw", "", "", "", ""
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        path = self._root_path / "calendar.csv"
        if not path.exists():
            raise MissingDataError(
                "calendar",
                f"calendar.csv not found at {path}",
            )
        try:
            df = pd.read_csv(path, dtype={"date": str})
        except Exception as exc:
            raise InvalidDataError(
                "calendar.csv",
                f"failed to read {path}: {exc}",
            ) from exc
        require_columns(df, ["date", "is_open"], name="calendar.csv")
        dates = [validate_yyyymmdd(v) for v in df["date"].tolist()]
        assert_unique_sorted(dates, name="calendar dates")
        flags = [str(v).strip().upper() for v in df["is_open"].tolist()]
        for flag in flags:
            if flag not in ("Y", "N"):
                raise InvalidDataError(
                    "calendar.is_open",
                    f"expected Y or N, got {flag!r}",
                )
        result = list(zip(dates, flags))
        self._cache.put(cache_key, result)
        return result

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
            path = self._root_path / "stock_list" / f"{date}.csv"
            if not path.exists():
                raise SnapshotFileMissingError("stock_list", str(path))
            try:
                df = pd.read_csv(path, dtype={"symbol": str, "date": str})
            except Exception as exc:
                raise InvalidDataError(
                    "stock_list",
                    f"failed to read {path}: {exc}",
                ) from exc
            require_columns(df, ["symbol", "date"], name="stock_list")
            # Filename date and CSV date column must agree.
            file_dates = {validate_yyyymmdd(v) for v in df["date"].tolist()}
            if file_dates != {date}:
                raise InvalidDataError(
                    "stock_list.date",
                    f"CSV date(s) {file_dates} do not match filename {date}",
                )
            symbols = [validate_symbol(v) for v in df["symbol"].tolist()]
            assert_unique_sorted(sorted(symbols), name="stock_list symbols")
            symbols.sort()
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
            - The window is intersected with the trading calendar; days
              inside the window with no row for `symbol` are simply absent
              from the result.
            - An empty result is a legitimate business outcome (the symbol
              did not trade at all in this window) and is returned as `[]`.
              It is NOT an error.
            - Whole-day snapshot files missing from disk are an
              **infrastructure** failure and must raise
              `SnapshotFileMissingError` so the engine can abort the run
              with a clear `DATA_ERROR` rather than silently fold the
              failure into a per-symbol gap.
            - A window with no trading days (or no bars for `symbol`)
              returns `[]` — an empty result is a legitimate business
              outcome, matching `InMemoryDataPortal`.

        Performance:
            - The portal maintains a per-symbol cumulative bar list; this
              method extends that list lazily and slices it with `bisect`,
              so per-call cost is O(log N) regardless of the window.
            - The same `Bar` objects are reused across overlapping
              queries; only the returned list is a defensive copy.
        """
        validate_symbol(symbol)
        validate_yyyymmdd(start, name="start")
        validate_yyyymmdd(end, name="end")
        if start > end:
            raise InvalidDataError("window", f"start {start} > end {end}")
        # Ensure the cumulative cache covers the requested window.
        self._ensure_symbol_bars(symbol, start, end)
        cumulative = self._symbol_bars[symbol]
        dates = [b.date for b in cumulative]
        idx_start = bisect_left(dates, start)
        idx_end = bisect_right(dates, end)
        return list(cumulative[idx_start:idx_end])

    def _ensure_symbol_bars(self, symbol: str, start: str, end: str) -> None:
        """Extend `_symbol_bars[symbol]` so it covers at least [start, end].

        Idempotent and lazy. The cumulative list is sorted ascending by
        date. Only the missing head/tail ranges are scanned — we never
        re-read files outside [start, end]. This is what guarantees each
        daily CSV is parsed at most once, and only when a query actually
        needs it.
        """
        cumulative = self._symbol_bars.get(symbol)
        if cumulative and cumulative[0].date <= start and cumulative[-1].date >= end:
            return
        if not cumulative:
            self._extend_symbol_bars(symbol, start, end)
            return
        # Extend the tail (end side) then the head (start side). Bounds are
        # inclusive; `_extend_symbol_bars` skips dates already cached, so
        # the boundary dates themselves are not re-read.
        if end > cumulative[-1].date:
            self._extend_symbol_bars(symbol, cumulative[-1].date, end)
        if start < cumulative[0].date:
            self._extend_symbol_bars(symbol, start, cumulative[0].date)

    def _extend_symbol_bars(self, symbol: str, start: str, end: str) -> None:
        """Add bars for `symbol` on trading days in [start, end] to the
        cumulative cache. `start` and `end` are concrete YYYYMMDD strings.
        """
        # Read the calendar slice for the requested range; this is cheap
        # because the underlying calendar is itself cached.
        calendar = self.get_calendar(start, end)
        if not calendar:
            return
        # Only consider days we don't yet have in the cumulative cache.
        existing = self._symbol_bars.get(symbol, [])
        have = {b.date for b in existing}
        for trading_day in calendar:
            if trading_day in have:
                continue
            bar = self._read_single_bar(symbol, trading_day)
            if bar is not None:
                existing.append(bar)
        # Keep the cumulative list sorted by date (stable insertion order
        # is preserved because `calendar` is sorted ascending and we only
        # append in that order).
        existing.sort(key=lambda b: b.date)
        self._symbol_bars[symbol] = existing

    def _read_single_bar(self, symbol: str, date: str) -> Optional[Bar]:
        """Return the bar for one (symbol, day) from the per-day cache.

        Returns None for a per-symbol gap (suspended / delisted / pre-IPO).
        Raises `SnapshotFileMissingError` when the daily file is missing on
        disk, and `InvalidDataError` for corrupt data.
        """
        per_day = self._daily_index.get(date)
        if per_day is not None:
            return per_day.get(symbol)
        # Cache miss for the day: parse the file once and populate.
        per_day = self._parse_daily_file(date)
        self._daily_index[date] = per_day
        return per_day.get(symbol)

    def _parse_daily_file(self, date: str) -> Dict[str, Bar]:
        """Parse `stock_daily/{date}.csv` into a `{symbol: Bar}` map.

        Raises `SnapshotFileMissingError` if the file is missing on disk,
        and `InvalidDataError` on corrupt data (missing columns, date
        mismatch, duplicate rows, or Bar invariant violations).
        """
        path = self._root_path / "stock_daily" / f"{date}.csv"
        if not path.exists():
            raise SnapshotFileMissingError("stock_daily", str(path))
        try:
            df = pd.read_csv(
                path,
                dtype={
                    "symbol": str,
                    "date": str,
                    "open": str,
                    "high": str,
                    "low": str,
                    "close": str,
                    "volume": str,
                },
            )
        except Exception as exc:
            raise InvalidDataError(
                "stock_daily",
                f"failed to read {path}: {exc}",
            ) from exc
        require_columns(
            df,
            ["symbol", "date", "open", "high", "low", "close", "volume"],
            name="stock_daily",
        )
        try:
            file_dates = {validate_yyyymmdd(v) for v in df["date"].tolist()}
        except InvalidDataError as exc:
            raise InvalidDataError("stock_daily.date", f"{path}: {exc}") from exc
        if file_dates != {date}:
            raise InvalidDataError(
                "stock_daily.date",
                f"CSV date(s) {file_dates} do not match filename {date}",
            )
        # Build a {symbol: Bar} map for the day. `itertuples` is roughly
        # 25x faster than `iterrows` on real-data snapshots. Duplicate
        # symbol rows are still rejected as a data error.
        result: Dict[str, Bar] = {}
        for row in df.itertuples(index=False):
            sym = getattr(row, "symbol")
            if sym in result:
                raise InvalidDataError(
                    "stock_daily",
                    f"{path}: duplicate row for {sym!r} on {date}",
                )
            try:
                result[sym] = Bar.from_raw(
                    symbol=sym,
                    date=getattr(row, "date"),
                    open=getattr(row, "open"),
                    high=getattr(row, "high"),
                    low=getattr(row, "low"),
                    close=getattr(row, "close"),
                    volume=int(getattr(row, "volume")),
                )
            except (ValueError, TypeError) as exc:
                raise InvalidDataError(
                    "stock_daily",
                    f"{path}: malformed row for {sym} on {date}: {exc}",
                ) from exc
        return result

    # ------------------------------------------------------------------ #
    # Factor
    # --------------------------------------------------------------------- #

    def get_factor(
        self, symbol: str, start: str, end: str
    ) -> List[Tuple[str, Decimal]]:
        """Return (date, factor) tuples for `symbol` in [start, end].

        Same gap semantics as `get_bars`: a per-symbol absence on a trading
        day simply omits that day from the result, while a missing whole-day
        factor file raises `SnapshotFileMissingError`.

        Performance: factor files are parsed once per day; the
        per-symbol cumulative view enables O(log N) window slicing.
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
        """Extend `_symbol_factors[symbol]` to cover at least [start, end]."""
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
        """Add factors for `symbol` on trading days in [start, end]."""
        calendar = self.get_calendar(start, end)
        if not calendar:
            return
        existing = self._symbol_factors.get(symbol, [])
        have = {d for d, _ in existing}
        for trading_day in calendar:
            if trading_day in have:
                continue
            factor = self._read_single_factor(symbol, trading_day)
            if factor is not None:
                existing.append((trading_day, factor))
        existing.sort(key=lambda x: x[0])
        self._symbol_factors[symbol] = existing

    def _read_single_factor(self, symbol: str, date: str) -> Optional[Decimal]:
        """Return the factor for one (symbol, day) from the per-day cache."""
        per_day = self._factor_index.get(date)
        if per_day is not None:
            return per_day.get(symbol)
        per_day = self._parse_factor_file(date)
        self._factor_index[date] = per_day
        return per_day.get(symbol)

    def _parse_factor_file(self, date: str) -> Dict[str, Decimal]:
        """Parse `stock_factor/{date}.csv` into a `{symbol: Decimal}` map."""
        path = self._root_path / "stock_factor" / f"{date}.csv"
        if not path.exists():
            raise SnapshotFileMissingError("stock_factor", str(path))
        try:
            df = pd.read_csv(path, dtype={"symbol": str, "date": str, "factor": str})
        except Exception as exc:
            raise InvalidDataError(
                "stock_factor",
                f"failed to read {path}: {exc}",
            ) from exc
        require_columns(df, ["symbol", "date", "factor"], name="stock_factor")
        file_dates = {validate_yyyymmdd(v) for v in df["date"].tolist()}
        if file_dates != {date}:
            raise InvalidDataError(
                "stock_factor.date",
                f"CSV date(s) {file_dates} do not match filename {date}",
            )
        result: Dict[str, Decimal] = {}
        # `itertuples` mirrors `_parse_daily_file` (~25x faster than
        # `iterrows` on real-data snapshots).
        for row in df.itertuples(index=False):
            sym = getattr(row, "symbol")
            if sym in result:
                raise InvalidDataError(
                    "stock_factor",
                    f"{path}: duplicate row for {sym!r} on {date}",
                )
            row_date = validate_yyyymmdd(getattr(row, "date"), name="factor.date")
            if row_date != date:
                raise InvalidDataError(
                    "factor.date",
                    f"expected {date}, got {row_date}",
                )
            raw = getattr(row, "factor")
            try:
                factor = Decimal(str(raw))
            except Exception as exc:
                raise InvalidDataError(
                    "factor.value",
                    f"{raw!r} is not Decimal",
                ) from exc
            if not factor.is_finite() or factor <= 0:
                raise InvalidDataError(
                    "factor.value",
                    f"non-positive or non-finite factor: {factor}",
                )
            result[sym] = factor
        return result
