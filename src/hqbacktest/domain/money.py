"""Decimal helpers enforcing contract rule 5.

Rules enforced here:
    - amounts, prices, fees and market value use decimal.Decimal exclusively;
    - `float` values are rejected outright (no silent Decimal(float));
    - cash amounts are quantized to 2 decimals (fen), prices to 4 decimals.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Union

CASH_QUANT = Decimal("0.01")
PRICE_QUANT = Decimal("0.0001")
LOT_SIZE = 100

NumberLike = Union[Decimal, str, int]


class MoneyError(ValueError):
    """Raised when monetary inputs violate contract rule 5."""


def to_decimal(value: NumberLike, *, name: str = "value") -> Decimal:
    """Convert a number-like input to Decimal.

    Accepted inputs: Decimal, str, int. Anything else (notably float) is rejected
    to avoid binary rounding leaking into the ledger.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise MoneyError(f"{name}: bool is not a valid monetary value")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise MoneyError(f"{name}={value!r} is not a valid Decimal string") from exc
    raise MoneyError(
        f"{name}={value!r}: type {type(value).__name__} cannot be converted to Decimal; "
        "pass str, int, or Decimal explicitly"
    )


def quantize_cash(value: NumberLike) -> Decimal:
    """Quantize a cash amount to 2 decimals (ROUND_HALF_EVEN)."""
    return to_decimal(value, name="cash").quantize(CASH_QUANT, rounding=ROUND_HALF_EVEN)


def quantize_price(value: NumberLike) -> Decimal:
    """Quantize a per-share price to 4 decimals (ROUND_HALF_EVEN)."""
    return to_decimal(value, name="price").quantize(
        PRICE_QUANT, rounding=ROUND_HALF_EVEN
    )


def is_positive(value: NumberLike) -> bool:
    """Return True iff the value is strictly greater than zero."""
    return to_decimal(value) > 0


def is_non_negative(value: NumberLike) -> bool:
    """Return True iff the value is greater than or equal to zero."""
    return to_decimal(value) >= 0


def round_lot(quantity: int, *, lot_size: int = LOT_SIZE) -> int:
    """Round a share quantity down to the nearest lot. Negative inputs become 0."""
    if quantity <= 0:
        return 0
    return (quantity // lot_size) * lot_size


def cash_for_trade(quantity: int, price: NumberLike) -> Decimal:
    """Compute the gross cash for a trade: quantity * price, quantized as cash."""
    if quantity <= 0:
        raise MoneyError(f"quantity must be positive, got {quantity}")
    return quantize_cash(Decimal(quantity) * to_decimal(price, name="price"))
