"""DataFrame row -> hqbacktest domain types.

`hqdata` CsvSource returns `pandas.DataFrame` with `float64` columns
(prices, change, pct_change, turnover) and `int64` / `str` columns
(volume, date). hqbacktest's domain layer (`Bar`, `Decimal` factor)
holds precision as a backtest invariant (contract §3.2 "Decimal precision").
Conversion goes through these helpers so the DataFrame -> domain boundary
is one place rather than scattered.

Conversion rules:
    - Prices / volume must be coerced through `str(...)` before `Decimal`
      to avoid binary-float inheritance (matches contract §3.5
      "禁止 Decimal(float(...))").
    - Prices are quantized to 4 decimals via `quantize_price` and volume
      is asserted `int` (1 lot = 100 shares; the hqdata `tushare`
      adapter already casts `volume` to `int64`).
    - Factor is required to be finite and strictly positive.
    - All malformed rows raise `data.errors.InvalidDataError` with a
      contextual message — never silently fold into a zero value.
"""

from decimal import Decimal
from typing import Any

import pandas as pd

from ..domain.bar import Bar
from .errors import InvalidDataError


def row_to_bar(row: pd.Series, *, source: str = "hqdata") -> Bar:
    """Build a `Bar` from one DataFrame row produced by hqdata.

    Expects columns: symbol, date, open, high, low, close, volume.
    Extra columns are ignored — hqdata's API contract defines additional
    columns (`pre_close`, `turnover`, `change`, `pct_change`) that hqbacktest
    may not need at this stage.
    """
    sym = row.get("symbol")
    if not isinstance(sym, str) or not sym:
        raise InvalidDataError(
            "bar.symbol",
            f"{source}: missing or empty symbol in row",
        )
    date = row.get("date")
    if not isinstance(date, str) or not date:
        raise InvalidDataError(
            "bar.date",
            f"{source}: missing date for {sym}",
        )
    try:
        return Bar.from_raw(
            symbol=sym,
            date=date,
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=int(row["volume"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise InvalidDataError(
            "bar.row",
            f"{source}: malformed bar for {sym} on {date}: {exc}",
        ) from exc


def value_to_factor(value: Any, *, symbol: str, date: str) -> Decimal:
    """Coerce one factor cell (`float64` or `int`/`str`) into a `Decimal`.

    Factor is required to be strictly positive and finite. Malformed
    values raise `InvalidDataError` — never coerced to 0 / 1 silently
    (silent coercion has the same effect as fabricating accounting
    entries, which is what contract rule 8 forbids).
    """
    try:
        factor = Decimal(str(value))
    except (TypeError, ValueError) as exc:
        raise InvalidDataError(
            "factor.value",
            f"{symbol} on {date}: {value!r} is not Decimal-coercible",
        ) from exc
    if not factor.is_finite() or factor <= 0:
        raise InvalidDataError(
            "factor.value",
            f"{symbol} on {date}: non-positive or non-finite factor: " f"{factor}",
        )
    return factor
