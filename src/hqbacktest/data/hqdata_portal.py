"""HqDataCsvPortal: production portal backed by hqdata CSV snapshots.

Rules enforced here (TODO task 4 contract):
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
      snapshot raises `MissingDataError` and never falls back to other dates.
    - Cache keys include the normalized `data_root` so two portals pointing
      at different roots cannot share entries.
"""

from datetime import date as _date
from decimal import Decimal
from pathlib import Path
from typing import List, Optional, Tuple

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
    """Map a source directory name to `(data_root, source_name)`."""
    if not isinstance(source, str) or not source:
        raise InvalidDataError("source", "must be a non-empty string")
    if Path(source).name != source or source in (".", ".."):
        raise InvalidDataError(
            "source",
            "must be a directory name; configure its parent with data_root",
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

        Per task 14: `.BJ` (Beijing Stock Exchange) symbols are excluded by
        default since v0.1 does not yet support BSE-specific trading rules
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

        Per-suspension / pre-IPO / post-delisting semantics (task 14):
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
            - Calendar queries that return no trading days still raise
              `MissingDataError` (caller asked for an empty window).
        """
        validate_symbol(symbol)
        validate_yyyymmdd(start, name="start")
        validate_yyyymmdd(end, name="end")
        if start > end:
            raise InvalidDataError("window", f"start {start} > end {end}")
        cache_key = CacheKey(
            self._data_root, self._source_name, "bars", symbol, "", start, end
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)
        calendar = self.get_calendar(start, end)
        if not calendar:
            raise MissingDataError("calendar", f"no trading days in [{start}, {end}]")
        bars: List[Bar] = []
        for trading_day in calendar:
            day_bars = self._read_bars_for_day(symbol, trading_day)
            bars.extend(day_bars)
        # Universe membership + per-row symbol sanity (defensive: a snapshot
        # should never claim a different symbol, but if it does we want the
        # error rather than a silent leak into the engine).
        for bar in bars:
            if bar.symbol != symbol:
                raise UnknownSymbolError(
                    f"CSV returned symbol {bar.symbol!r} when {symbol!r} was requested"
                )
        # Empty result is legal; cache and return a defensive copy.
        self._cache.put(cache_key, list(bars))
        return list(bars)

    def _read_bars_for_day(self, symbol: str, date: str) -> List[Bar]:
        """Read the bars for one (symbol, day).

        Distinct failure modes (task 14):
            - `stock_daily/{date}.csv` is missing entirely
              -> `SnapshotFileMissingError` (data infrastructure failure).
            - The file exists but has no row for `symbol` (suspended,
              delisted, not yet listed)
              -> `[]`; this is a per-symbol gap and is a normal business
              outcome, NOT an error.
            - The file exists but is corrupt (missing column, bad decimal,
              symbol-row count mismatch, Bar invariants violated)
              -> `InvalidDataError`; the engine aborts the run via the
              caller's RunFailed wrapping.
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
        # Filename date and CSV date column must agree.
        try:
            file_dates = {validate_yyyymmdd(v) for v in df["date"].tolist()}
        except InvalidDataError as exc:
            raise InvalidDataError("stock_daily.date", f"{path}: {exc}") from exc
        if file_dates != {date}:
            raise InvalidDataError(
                "stock_daily.date",
                f"CSV date(s) {file_dates} do not match filename {date}",
            )
        # Filter to the requested symbol; if no row matches it is a per-
        # symbol gap (suspended / delisted / pre-IPO), NOT an error.
        rows = df[df["symbol"] == symbol]
        if len(rows) > 1:
            raise InvalidDataError(
                "stock_daily",
                f"expected at most one row for {symbol!r} on {date}, got {len(rows)}",
            )
        out: List[Bar] = []
        for _, row in rows.iterrows():
            try:
                out.append(
                    Bar.from_raw(
                        symbol=str(row["symbol"]),
                        date=str(row["date"]),
                        open=str(row["open"]),
                        high=str(row["high"]),
                        low=str(row["low"]),
                        close=str(row["close"]),
                        volume=int(row["volume"]),
                    )
                )
            except (ValueError, TypeError) as exc:
                # Bar invariants violated; wrap with file/line context so
                # the audit trail pinpoints the bad row.
                raise InvalidDataError(
                    "stock_daily",
                    f"{path}: malformed row for {row.get('symbol', '?')} "
                    f"on {row.get('date', '?')}: {exc}",
                ) from exc
        return out

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
        """
        validate_symbol(symbol)
        validate_yyyymmdd(start, name="start")
        validate_yyyymmdd(end, name="end")
        if start > end:
            raise InvalidDataError("window", f"start {start} > end {end}")
        cache_key = CacheKey(
            self._data_root, self._source_name, "factor", symbol, "", start, end
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)
        calendar = self.get_calendar(start, end)
        if not calendar:
            raise MissingDataError("factor", f"no trading days in [{start}, {end}]")
        rows: List[Tuple[str, Decimal]] = []
        for trading_day in calendar:
            path = self._root_path / "stock_factor" / f"{trading_day}.csv"
            if not path.exists():
                raise SnapshotFileMissingError("stock_factor", str(path))
            try:
                df = pd.read_csv(
                    path, dtype={"symbol": str, "date": str, "factor": str}
                )
            except Exception as exc:
                raise InvalidDataError(
                    "stock_factor",
                    f"failed to read {path}: {exc}",
                ) from exc
            require_columns(df, ["symbol", "date", "factor"], name="stock_factor")
            file_dates = {validate_yyyymmdd(v) for v in df["date"].tolist()}
            if file_dates != {trading_day}:
                raise InvalidDataError(
                    "stock_factor.date",
                    f"CSV date(s) {file_dates} do not match filename {trading_day}",
                )
            symbol_rows = df[df["symbol"] == symbol]
            if symbol_rows.empty:
                # Per-symbol gap; this is a normal business outcome.
                continue
            if len(symbol_rows) != 1:
                raise InvalidDataError(
                    "factor",
                    f"expected one row for {symbol!r} on {trading_day}, got {len(symbol_rows)}",
                )
            row = symbol_rows.iloc[0]
            row_date = validate_yyyymmdd(row["date"], name="factor.date")
            if row_date != trading_day:
                raise InvalidDataError(
                    "factor.date",
                    f"expected {trading_day}, got {row_date}",
                )
            try:
                factor = Decimal(str(row["factor"]))
            except Exception as exc:
                raise InvalidDataError(
                    "factor.value",
                    f"{row['factor']!r} is not Decimal",
                ) from exc
            if not factor.is_finite() or factor <= 0:
                raise InvalidDataError(
                    "factor.value",
                    f"non-positive or non-finite factor: {factor}",
                )
            rows.append((row_date, factor))
        self._cache.put(cache_key, list(rows))
        return list(rows)
