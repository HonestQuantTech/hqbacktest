"""Validators for DataFrames loaded from hqdata CSV snapshots.

The portal layer is responsible for catching every malformed piece of data
_before_ it enters the engine, so validators here are deliberately strict
(missing column, NaN value, non-positive price, duplicate date, etc.). All
violations raise `InvalidDataError` with a diagnostic detail string.
"""

from decimal import Decimal
from typing import Iterable, Sequence

from .errors import InvalidDataError

SYMBOL_SUFFIXES = (".SH", ".SZ", ".BJ")


def validate_yyyymmdd(value: object, *, name: str = "date") -> str:
    """Return the value if it is a valid YYYYMMDD string, else raise."""
    if not isinstance(value, str):
        raise InvalidDataError(
            name, f"must be YYYYMMDD string, got {type(value).__name__}"
        )
    if len(value) != 8 or not value.isdigit():
        raise InvalidDataError(name, f"must be 8 digits, got {value!r}")
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
