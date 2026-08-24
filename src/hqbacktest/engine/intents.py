"""Order intent helpers.

Pure functions for converting a strategy's quantitative wish (cash value,
target percentage, etc.) into a concrete share count using the current
`DataView`. These helpers are deliberately free-standing so they can be
unit-tested without spinning up a `Context`.

The actual `Order` object is built by `Context.order(...)` after this
computation succeeds; helpers here never touch the ledger directly.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..domain.enums import Side
from ..domain.money import round_lot
from .errors import StrategyLifecycleError


def quantity_from_value(value: Decimal, price: Decimal, lot_size: int = 100) -> int:
    """Convert a signed cash `value` into a lot-aligned signed share count.

    Positive `value` => BUY; negative => SELL. The result is rounded down to
    the nearest lot so we never request fractional shares.
    """
    if not isinstance(value, Decimal):
        raise StrategyLifecycleError(
            f"value must be Decimal, got {type(value).__name__}"
        )
    if price <= 0:
        raise StrategyLifecycleError(f"price must be positive, got {price}")
    if value == 0:
        return 0
    sign = 1 if value > 0 else -1
    raw = abs(value) / price
    # Use Decimal arithmetic to avoid float rounding.
    lot_value = Decimal(lot_size)
    lots = int(raw / lot_value)  # floor for positive quantities
    return sign * lots * lot_size


def side_from_quantity(quantity: int) -> Optional[Side]:
    """Map a signed share count to its order side."""
    if quantity > 0:
        return Side.BUY
    if quantity < 0:
        return Side.SELL
    return None


def signed_diff_to_lots(target: int, current: int, lot_size: int = 100) -> int:
    """Compute the signed delta between `target` and `current`, lot-aligned.

    Used by `order_target*` helpers: the resulting value is the share count
    to send through the broker (positive => BUY, negative => SELL).
    """
    diff = target - current
    if diff == 0:
        return 0
    sign = 1 if diff > 0 else -1
    abs_diff = abs(diff)
    lots = abs_diff // lot_size  # floor; contract: lot-aligned BUY/SELL
    return sign * lots * lot_size


def target_quantity_for_value(
    target_value: Decimal, price: Decimal, current_quantity: int, lot_size: int = 100
) -> int:
    """Compute the desired `quantity` for a `target_value` position.

    `target_value` may be `Decimal("0")` to flatten the position.
    """
    if price <= 0:
        raise StrategyLifecycleError(f"price must be positive, got {price}")
    if target_value < 0:
        raise StrategyLifecycleError(
            f"target_value must be non-negative, got {target_value}"
        )
    if target_value == 0:
        return current_quantity  # flat → no change
    target_qty_signed = quantity_from_value(target_value, price, lot_size)
    # quantity_from_value already returns a lot-aligned signed count.
    # For a target_value (positive), the signed count is positive.
    return target_qty_signed


def target_value_for_percent(percent: Decimal, total_equity: Decimal) -> Decimal:
    """Convert a fraction (0..1) of total equity to a target cash value."""
    if percent < 0 or percent > 1:
        raise StrategyLifecycleError(f"percent must be in [0, 1], got {percent}")
    return (percent * total_equity).quantize(Decimal("0.01"))


def normalize_percent(percent) -> Decimal:
    """Coerce numeric percentages (0.95 / "95" / Decimal) into a fraction."""
    if isinstance(percent, Decimal):
        return percent
    if isinstance(percent, float):
        raise StrategyLifecycleError(
            f"percent must not be float (contract rule 5); got {percent!r}"
        )
    try:
        return Decimal(str(percent))
    except Exception as exc:
        raise StrategyLifecycleError(
            f"percent must be Decimal/str/int, got {type(percent).__name__}"
        ) from exc
