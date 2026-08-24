"""AccountSnapshot: read-only view of the ledger at end of trading day."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Tuple

from .money import quantize_cash, quantize_price


@dataclass(frozen=True)
class PositionSnapshot:
    """A per-symbol slice of an AccountSnapshot."""

    symbol: str
    quantity: int
    sellable_quantity: int
    avg_cost: Decimal
    market_price: Decimal
    market_value: Decimal


@dataclass(frozen=True)
class AccountSnapshot:
    """Immutable record of the portfolio at end of a trading day.

    `positions` is a tuple of PositionSnapshot so the snapshot can be hashed
    and compared across days.
    """

    date: str  # YYYYMMDD
    cash: Decimal
    frozen_cash: Decimal
    market_value: Decimal
    total_equity: Decimal
    realized_pnl: Decimal
    position_count: int
    positions: Tuple[PositionSnapshot, ...]

    def __post_init__(self) -> None:
        if len(self.date) != 8 or not self.date.isdigit():
            raise ValueError(f"date must be YYYYMMDD, got {self.date!r}")
        for name in (
            "cash",
            "frozen_cash",
            "market_value",
            "total_equity",
            "realized_pnl",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                raise ValueError(f"{name} must be Decimal, got {type(value).__name__}")
        if self.position_count != len(self.positions):
            raise ValueError(
                f"position_count ({self.position_count}) disagrees with "
                f"len(positions) ({len(self.positions)})"
            )

    def position(self, symbol: str) -> PositionSnapshot:
        for snap in self.positions:
            if snap.symbol == symbol:
                return snap
        raise KeyError(symbol)

    @classmethod
    def build(
        cls,
        *,
        date: str,
        cash: Decimal,
        frozen_cash: Decimal,
        realized_pnl: Decimal,
        prices: Dict[str, Decimal],
        positions: Dict[str, Tuple[int, int, Decimal]],
    ) -> "AccountSnapshot":
        """Build a snapshot from flat tuples.

        `positions` maps symbol to (quantity, sellable_quantity, avg_cost).
        `prices` provides the closing price used to value each holding.
        """
        snaps: list[PositionSnapshot] = []
        market_total = Decimal(0)
        for symbol, (qty, sellable, avg_cost) in positions.items():
            price = prices.get(symbol)
            if qty <= 0:
                continue
            if price is None:
                raise ValueError(f"missing market price for held position {symbol}")
            if not isinstance(price, Decimal) or price <= 0:
                raise ValueError(f"invalid market price for {symbol}: {price!r}")
            mv = quantize_cash(Decimal(price) * Decimal(qty))
            snaps.append(
                PositionSnapshot(
                    symbol=symbol,
                    quantity=qty,
                    sellable_quantity=sellable,
                    avg_cost=avg_cost,
                    market_price=quantize_price(price),
                    market_value=mv,
                )
            )
            market_total += mv
        market_total = quantize_cash(market_total)
        return cls(
            date=date,
            cash=quantize_cash(cash),
            frozen_cash=quantize_cash(frozen_cash),
            market_value=market_total,
            total_equity=quantize_cash(cash + market_total),
            realized_pnl=quantize_cash(realized_pnl),
            position_count=len(snaps),
            positions=tuple(snaps),
        )
