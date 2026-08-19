"""Fill: an immutable record of a single execution against an Order."""

from dataclasses import dataclass
from decimal import Decimal

from .enums import EventType, Side
from .money import cash_for_trade, quantize_cash, quantize_price


@dataclass(frozen=True)
class Fill:
    """A single execution against an Order.

    For v0.1 market orders, an Order produces 0 or 1 Fill. The model already
    carries the fields needed for future partial fills.
    """

    fill_id: str
    order_id: str
    symbol: str
    side: Side
    quantity: int
    price: Decimal
    amount: Decimal  # signed gross: BUY positive, SELL negative
    commission: Decimal
    stamp_tax: Decimal
    other_fee: Decimal
    filled_at: str  # YYYYMMDD
    session: EventType

    def __post_init__(self) -> None:
        if not self.fill_id:
            raise ValueError("fill_id must be non-empty")
        if not self.order_id:
            raise ValueError("order_id must be non-empty")
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if not isinstance(self.side, Side):
            raise ValueError(f"side must be Side, got {type(self.side).__name__}")
        if not isinstance(self.session, EventType):
            raise ValueError(
                f"session must be EventType, got {type(self.session).__name__}"
            )
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool):
            raise ValueError(
                f"quantity must be int, got {type(self.quantity).__name__}"
            )
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
        for field_name in ("price", "amount", "commission", "stamp_tax", "other_fee"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                raise ValueError(
                    f"{field_name} must be Decimal, got {type(value).__name__}"
                )
        if self.commission < 0 or self.stamp_tax < 0 or self.other_fee < 0:
            raise ValueError("fees must be non-negative")
        if self.price <= 0:
            raise ValueError("price must be positive")
        if self.price != quantize_price(self.price):
            raise ValueError("price must be quantized to 4 decimal places")
        for field_name in ("amount", "commission", "stamp_tax", "other_fee"):
            value = getattr(self, field_name)
            if value != quantize_cash(value):
                raise ValueError(f"{field_name} must be quantized to 2 decimal places")
        if len(self.filled_at) != 8 or not self.filled_at.isdigit():
            raise ValueError(f"filled_at must be YYYYMMDD, got {self.filled_at!r}")
        gross = cash_for_trade(self.quantity, self.price)
        expected_amount = gross if self.side is Side.BUY else -gross
        if self.amount != expected_amount:
            raise ValueError(
                f"amount ({self.amount}) must equal signed gross ({expected_amount})"
            )

    @classmethod
    def from_trade(
        cls,
        *,
        fill_id: str,
        order_id: str,
        symbol: str,
        side: Side,
        quantity: int,
        price: Decimal,
        commission: Decimal,
        stamp_tax: Decimal,
        other_fee: Decimal,
        filled_at: str,
        session: EventType,
    ) -> "Fill":
        """Build a Fill with consistent price/amount/fees quantization."""
        quantized_price = quantize_price(price)
        gross = quantize_cash(Decimal(quantity) * quantized_price)
        signed_amount = gross if side is Side.BUY else -gross
        return cls(
            fill_id=fill_id,
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=quantized_price,
            amount=signed_amount,
            commission=quantize_cash(commission),
            stamp_tax=quantize_cash(stamp_tax),
            other_fee=quantize_cash(other_fee),
            filled_at=filled_at,
            session=session,
        )

    def total_fee(self) -> Decimal:
        return self.commission + self.stamp_tax + self.other_fee

    def net_amount(self) -> Decimal:
        """Signed net cash flow from the portfolio's perspective.

        For BUY: cash decreases by amount + commission + other_fee
        (returns a negative number).
        For SELL: cash increases by |amount| - commission - stamp_tax - other_fee
        (returns a positive number).
        """
        if self.side is Side.BUY:
            return -(self.amount + self.commission + self.other_fee)
        return -self.amount - self.commission - self.stamp_tax - self.other_fee
