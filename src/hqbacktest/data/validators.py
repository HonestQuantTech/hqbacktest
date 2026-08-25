"""Validators for DataFrames loaded from hqdata CSV snapshots.

The portal layer is responsible for catching every malformed piece of data
_before_ it enters the engine, so validators here are deliberately strict
(missing column, NaN value, non-positive price, duplicate date, etc.). All
violations raise `InvalidDataError` with a diagnostic detail string.
"""

from datetime import datetime
from decimal import Decimal
from typing import Iterable, Sequence

from .errors import InvalidDataError

SYMBOL_SUFFIXES = (".SH", ".SZ", ".BJ")

# Sentinel that `DataView` (and `Scheduler`) treat as "no data visible
# yet" — the first day before any snapshot is loaded. We allow it
# through `validate_yyyymmdd` so engine-internal code paths can compare
# against it without tripping on a calendar check (`strptime("00000000",
# "%Y%m%d")` raises `ValueError` in Python ≥ 3, because year 0 is
# disallowed).
SENTINEL_NO_HISTORY = "00000000"


def validate_yyyymmdd(value: object, *, name: str = "date") -> str:
    """Return the value if it is a valid YYYYMMDD string, else raise.

    Task 22.3: rejects 8-digit strings that are NOT real calendar
    dates (e.g. ``"20241399"`` month 13, ``"20240230"`` Feb 30 in a
    non-leap year). Previously the validator only checked
    ``len == 8 and isdigit()`` so impossible dates silently slipped
    through and were only caught downstream — sometimes by an
    unrelated check that masked the real defect.

    The sentinel ``"00000000"`` is explicitly accepted (see
    `SENTINEL_NO_HISTORY`) because it is a legal engine-internal value
    for `DataView.visible_through` and `Scheduler`'s pre-start phase.
    """
    if not isinstance(value, str):
        raise InvalidDataError(
            name, f"must be YYYYMMDD string, got {type(value).__name__}"
        )
    if len(value) != 8 or not value.isdigit():
        raise InvalidDataError(name, f"must be 8 digits, got {value!r}")
    if value == SENTINEL_NO_HISTORY:
        return value
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise InvalidDataError(
            name,
            f"not a valid calendar date: {value!r} ({exc})",
        ) from exc
    return value


def validate_symbol(value: object) -> str:
    """Return the symbol if it matches the contract format, else raise."""
    if not isinstance(value, str) or not value:
        raise InvalidDataError("symbol", f"must be non-empty string, got {value!r}")
    if len(value) != 9:
        raise InvalidDataError(
            "symbol", f"must be 9 chars (e.g. 600000.SH), got {value!r}"
        )
    if not value[:6].isdigit():
        raise InvalidDataError("symbol", f"first 6 chars must be digits, got {value!r}")
    if not value.endswith(SYMBOL_SUFFIXES):
        raise InvalidDataError(
            "symbol",
            f"suffix must be one of {SYMBOL_SUFFIXES}, got {value!r}",
        )
    return value


def require_columns(df, expected: Iterable[str], *, name: str) -> None:
    """Raise if any expected column is missing from the DataFrame."""
    missing = [col for col in expected if col not in df.columns]
    if missing:
        raise InvalidDataError(name, f"missing columns: {missing}")


def assert_unique_sorted(values: Sequence, *, name: str) -> None:
    """Raise if the sequence has duplicates or is not ascending."""
    if len(values) < 2:
        return
    for prev, curr in zip(values, values[1:]):
        if prev >= curr:
            raise InvalidDataError(
                name, f"values must be strictly ascending; found {prev} >= {curr}"
            )


def validate_decimal_series(values, *, name: str) -> None:
    """Raise on NaN / non-positive values in a numeric pandas Series."""
    if hasattr(values, "isna"):
        if values.isna().any():
            raise InvalidDataError(name, "contains NaN")
    for v in values:
        if not isinstance(v, Decimal):
            # pandas stores floats here; coerce to Decimal then check.
            try:
                d = Decimal(str(v))
            except Exception as exc:
                raise InvalidDataError(
                    name, f"value {v!r} is not coercible to Decimal"
                ) from exc
        else:
            d = v
        if d <= 0:
            raise InvalidDataError(name, f"non-positive value: {d}")
