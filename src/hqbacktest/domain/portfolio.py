"""Portfolio: cash, frozen cash and per-symbol positions.

Portfolio is the single ledger source of truth for cash and shares. The engine
must call `apply_fill` and `settle_t1` rather than mutating fields directly.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, Optional

from .enums import EventType, Side
from .fill import Fill
from .money import quantize_cash
from .position import Position


@dataclass(init=False)
class Portfolio:
    """Cash + positions ledger for a single backtest run."""

    initial_cash: Decimal
    cash: Decimal
    frozen_cash: Decimal = field(default=Decimal(0))
    positions: Dict[str, Position] = field(default_factory=dict)
    realized_pnl: Decimal = field(default=Decimal(0))

    def __init__(
        self,
        initial_cash: Decimal,
        cash: Optional[Decimal] = None,
        frozen_cash: Decimal = Decimal(0),
        positions: Optional[Dict[str, Position]] = None,
        realized_pnl: Decimal = Decimal(0),
    ) -> None:
        self.initial_cash = initial_cash
        self.cash = initial_cash if cash is None else cash
        self.frozen_cash = frozen_cash
        self.positions = {} if positions is None else positions
        self.realized_pnl = realized_pnl
        self.__post_init__()

    def __post_init__(self) -> None:
        if not isinstance(self.initial_cash, Decimal):
            raise ValueError(
                f"initial_cash must be Decimal, got {type(self.initial_cash).__name__}"
            )
        if self.initial_cash < 0:
            raise ValueError("initial_cash must be non-negative")
        if not isinstance(self.cash, Decimal) or self.cash < 0:
            raise ValueError(f"cash must be non-negative Decimal, got {self.cash!r}")
        if not isinstance(self.frozen_cash, Decimal) or self.frozen_cash < 0:
            raise ValueError(
                f"frozen_cash must be non-negative Decimal, got {self.frozen_cash!r}"
            )
        if self.frozen_cash > self.cash:
            raise ValueError("frozen_cash cannot exceed cash")
        if not isinstance(self.realized_pnl, Decimal):
            raise ValueError(
                "realized_pnl must be Decimal, "
                f"got {type(self.realized_pnl).__name__}"
            )
        if any(
            not isinstance(symbol, str) or not isinstance(position, Position)
            for symbol, position in self.positions.items()
        ):
            raise ValueError("positions must map symbol strings to Position instances")

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol)
        return self.positions[symbol]

    def market_value(self, prices: Dict[str, Decimal]) -> Decimal:
        total = Decimal(0)
        for symbol, position in self.positions.items():
            if position.quantity == 0:
                continue
            if symbol not in prices:
                continue
            total += position.market_value(prices[symbol])
        return quantize_cash(total)

    def total_equity(self, prices: Dict[str, Decimal]) -> Decimal:
        return quantize_cash(self.cash + self.market_value(prices))

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #

    def reserve_cash(self, amount: Decimal) -> None:
        """Reserve cash for a pending buy."""
        amount = quantize_cash(amount)
        if amount > self.cash - self.frozen_cash:
            raise ValueError(
                f"cannot reserve {amount}; available {self.cash - self.frozen_cash}"
            )
        self.frozen_cash = quantize_cash(self.frozen_cash + amount)

    def release_cash(self, amount: Decimal) -> None:
        amount = quantize_cash(amount)
        if amount > self.frozen_cash:
            raise ValueError(
                f"cannot release {amount}; frozen balance {self.frozen_cash}"
            )
        self.frozen_cash = quantize_cash(self.frozen_cash - amount)

    def apply_fill(self, fill: Fill) -> None:
        """Apply a Fill: update cash, positions, frozen cash, realized pnl."""
        if not isinstance(fill, Fill):
            raise ValueError(f"expected Fill, got {type(fill).__name__}")
        if fill.session is not EventType.OPEN_MATCH:
            raise ValueError(
                f"v0.1 only accepts OPEN_MATCH fills, got {fill.session.name}"
            )
        if fill.side is Side.BUY:
            cost = fill.amount + fill.commission + fill.other_fee
            if cost > self.cash:
                raise ValueError(
                    f"insufficient cash for fill: need {cost}, have {self.cash}"
                )
            position = self.get_position(fill.symbol)
            self.cash = quantize_cash(self.cash - cost)
            self.frozen_cash = quantize_cash(max(Decimal(0), self.frozen_cash - cost))
            position.update_buy(fill.quantity, fill.price)
        else:
            position = self.positions.get(fill.symbol)
            if position is None:
                raise ValueError(f"no position available to sell for {fill.symbol}")
            proceeds = -fill.amount - fill.commission - fill.stamp_tax - fill.other_fee
            prior_realized = position.realized_pnl
            position.update_sell(fill.quantity, fill.price)
            delta = position.realized_pnl - prior_realized
            self.cash = quantize_cash(self.cash + proceeds)
            self.realized_pnl = quantize_cash(self.realized_pnl + delta)

    def settle_t1(self, today: str, previous_date: Optional[str]) -> None:
        """End-of-day settlement.

        Rolls any positions' pending_today_buy into sellable_quantity. This must
        be called once per trading day AFTER all fills but BEFORE the next day's
        BEFORE_TRADING_START callback.
        """
        del today, previous_date  # kept for future use
        for position in self.positions.values():
            position.settle_t1()

    def iter_positions(self) -> Iterable[Position]:
        return self.positions.values()
