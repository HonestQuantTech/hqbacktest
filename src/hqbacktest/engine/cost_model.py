"""CostModel: pluggable fee computation (task 8).

Every fill carries three fees:
    * `commission`   - per-side, proportional to turnover with a floor.
    * `stamp_tax`    - SELL side only (印花税, China A-share).
    * `transfer_fee` - per-side, currently unused (过户费, opt-in).

The default v0.1 cost model exposes:
    * commission_rate   = 0.025%  of gross turnover
    * min_commission    = 5.00 CNY per side
    * stamp_tax_rate    = 0.1%    of gross turnover (SELL only)
    * transfer_fee_rate = 0.0     (disabled in v0.1)
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ..domain.enums import Side
from ..domain.money import quantize_cash
from ..domain.order import Order


@dataclass(frozen=True)
class Cost:
    """Per-fill fee breakdown."""

    commission: Decimal
    stamp_tax: Decimal
    transfer_fee: Decimal


@runtime_checkable
class CostModel(Protocol):
    """Compute per-fill fees for one order at a given price."""

    def compute(self, order: Order, price: Decimal, quantity: int) -> Cost: ...


@dataclass(frozen=True)
class DefaultCostModel:
    """v0.1 default cost model.

    All rates are explicit configuration; nothing is hidden in code
    constants (TODO task 8 verification).
    """

    commission_rate: Decimal = Decimal("0.00025")  # 0.025% of turnover
    min_commission: Decimal = Decimal("5.00")  # floor per side
    stamp_tax_rate: Decimal = Decimal("0.001")  # 0.1% on SELL only
    transfer_fee_rate: Decimal = Decimal("0.0")  # disabled in v0.1

    def compute(self, order: Order, price: Decimal, quantity: int) -> Cost:
        if quantity <= 0:
            raise ValueError(f"quantity must be positive, got {quantity}")
        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        gross = Decimal(quantity) * price
        commission = max(gross * self.commission_rate, self.min_commission)
        commission = quantize_cash(commission)
        if order.side is Side.SELL:
            stamp_tax = quantize_cash(gross * self.stamp_tax_rate)
        else:
            stamp_tax = Decimal("0.00")
        transfer_fee = quantize_cash(gross * self.transfer_fee_rate)
        return Cost(
            commission=commission,
            stamp_tax=stamp_tax,
            transfer_fee=transfer_fee,
        )
