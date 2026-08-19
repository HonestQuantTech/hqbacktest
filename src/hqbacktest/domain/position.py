"""Position: per-symbol holdings with T+1 sellable accounting.

Position is mutable because trades update it; ledger operations go through
`update_buy`, `update_sell` and `settle_t1` so all changes are auditable and
consistent with the rest of the domain.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict

from .money import PRICE_QUANT, quantize_cash, quantize_price


@dataclass
class Position:
    """Per-symbol holdings.

    `quantity` is the total shares held at end of trading day;
    `sellable_quantity` is the subset available for sale today (T+1).
    `pending_today_buy` tracks shares bought today that become sellable next day.
    """

    symbol: str
    quantity: int = 0
    sellable_quantity: int = 0
    avg_cost: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    pending_today_buy: int = field(default=0)

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        for name in ("quantity", "sellable_quantity", "pending_today_buy"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be int, got {type(value).__name__}")
        if self.quantity < 0 or self.sellable_quantity < 0:
            raise ValueError("quantities must be non-negative")
        if self.sellable_quantity > self.quantity:
            raise ValueError(
                f"sellable_quantity ({self.sellable_quantity}) > quantity ({self.quantity})"
            )
        if self.pending_today_buy < 0:
            raise ValueError("pending_today_buy must be non-negative")
        if self.pending_today_buy > self.quantity - self.sellable_quantity:
            raise ValueError(
                "pending_today_buy cannot exceed shares that are not yet sellable"
            )
        if not isinstance(self.avg_cost, Decimal) or self.avg_cost < 0:
            raise ValueError(
                f"avg_cost must be non-negative Decimal, got {self.avg_cost!r}"
            )
        if not isinstance(self.realized_pnl, Decimal):
            raise ValueError(
                "realized_pnl must be Decimal, "
                f"got {type(self.realized_pnl).__name__}"
            )

    # ------------------------------------------------------------------ #
    # Trade mutations
    # ------------------------------------------------------------------ #

    def update_buy(self, quantity: int, price: Decimal) -> None:
        """Record a buy. Updates weighted avg cost; adds to today's pending buy."""
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise ValueError(f"buy quantity must be int, got {type(quantity).__name__}")
        if quantity <= 0:
            raise ValueError(f"buy quantity must be positive, got {quantity}")
        if not isinstance(price, Decimal) or price <= 0:
            raise ValueError(f"buy price must be positive Decimal, got {price!r}")
        quantized_price = quantize_price(price)
        new_quantity = self.quantity + quantity
        if self.quantity == 0:
            self.avg_cost = quantized_price
        else:
            total = self.avg_cost * Decimal(self.quantity) + quantized_price * Decimal(
                quantity
            )
            self.avg_cost = (total / Decimal(new_quantity)).quantize(PRICE_QUANT)
        self.quantity = new_quantity
        self.pending_today_buy += quantity

    def update_sell(self, quantity: int, price: Decimal) -> None:
        """Record a sell. Reduces sellable quantity first (T+1 enforced)."""
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise ValueError(
                f"sell quantity must be int, got {type(quantity).__name__}"
            )
        if quantity <= 0:
            raise ValueError(f"sell quantity must be positive, got {quantity}")
        if not isinstance(price, Decimal) or price <= 0:
            raise ValueError(f"sell price must be positive Decimal, got {price!r}")
        if quantity > self.sellable_quantity:
            raise ValueError(
                f"insufficient sellable quantity: have {self.sellable_quantity}, "
                f"want {quantity}"
            )
        quantized_price = quantize_price(price)
        realized = (quantized_price - self.avg_cost) * Decimal(quantity)
        self.realized_pnl = (self.realized_pnl + realized).quantize(Decimal("0.01"))
        self.sellable_quantity -= quantity
        self.quantity -= quantity
        if self.quantity == 0:
            self.avg_cost = Decimal(0)

    def settle_t1(self) -> None:
        """Roll today's buys into the sellable pool for the next trading day."""
        if self.pending_today_buy == 0:
            return
        self.sellable_quantity += self.pending_today_buy
        self.pending_today_buy = 0

    def market_value(self, price: Decimal) -> Decimal:
        """Compute current market value at `price` (Decimal)."""
        if self.quantity == 0:
            return Decimal("0.00")
        return quantize_cash(quantize_price(price) * Decimal(self.quantity))


def empty_positions() -> Dict[str, Position]:
    """Return a fresh positions map keyed by symbol."""
    return {}
