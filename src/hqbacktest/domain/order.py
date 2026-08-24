"""Order model with explicit lifecycle.

The Order object carries its current status plus timestamps for every state
change. Transitions go through `Order.transition`, which calls the state machine
in `state_machine.py`. Every transition carries the trading day it happened on
(YYYYMMDD) so the audit trail never depends on wall-clock time.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from .enums import EventType, OrderStatus, OrderType, RejectReason, Side
from .money import PRICE_QUANT
from .state_machine import validate_transition


@dataclass
class Order:
    """A single strategy-issued order.

    `filled_quantity` accumulates across fills; `avg_fill_price` is recomputed
    on every fill using a running weighted average.
    """

    order_id: str
    symbol: str
    side: Side
    quantity: int
    order_type: OrderType
    created_at: str  # YYYYMMDD
    created_session: EventType

    status: OrderStatus = OrderStatus.NEW
    filled_quantity: int = 0
    avg_fill_price: Optional[Decimal] = None

    accepted_at: Optional[str] = None
    pending_at: Optional[str] = None
    partially_filled_at: Optional[str] = None
    filled_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    rejected_at: Optional[str] = None

    reject_reason: Optional[RejectReason] = None
    reject_detail: Optional[str] = None

    fill_ids: List[str] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.order_id:
            raise ValueError("order_id must be non-empty")
        if not self.symbol or not isinstance(self.symbol, str):
            raise ValueError(f"symbol must be a non-empty string, got {self.symbol!r}")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool):
            raise ValueError(
                f"quantity must be int, got {type(self.quantity).__name__}"
            )
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
        if not isinstance(self.side, Side):
            raise ValueError(f"side must be Side, got {type(self.side).__name__}")
        if not isinstance(self.order_type, OrderType):
            raise ValueError(
                f"order_type must be OrderType, got {type(self.order_type).__name__}"
            )
        if not isinstance(self.created_session, EventType):
            raise ValueError(
                "created_session must be EventType, "
                f"got {type(self.created_session).__name__}"
            )
        self._validate_date(self.created_at, "created_at")
        if self.status is not OrderStatus.NEW:
            raise ValueError("new orders must start in NEW status")
        if not isinstance(self.filled_quantity, int) or isinstance(
            self.filled_quantity, bool
        ):
            raise ValueError(
                "filled_quantity must be int, "
                f"got {type(self.filled_quantity).__name__}"
            )
        if self.filled_quantity < 0 or self.filled_quantity > self.quantity:
            raise ValueError(
                f"filled_quantity ({self.filled_quantity}) must be in [0, {self.quantity}]"
            )
        if self.avg_fill_price is not None and not isinstance(
            self.avg_fill_price, Decimal
        ):
            raise ValueError(
                f"avg_fill_price must be Decimal, got {type(self.avg_fill_price).__name__}"
            )

    @staticmethod
    def _validate_date(value: str, name: str) -> None:
        if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
            raise ValueError(f"{name} must be YYYYMMDD, got {value!r}")

    # ------------------------------------------------------------------ #
    # Lifecycle helpers
    # ------------------------------------------------------------------ #

    def transition(
        self,
        target: OrderStatus,
        *,
        at: str,
        reason: Optional[RejectReason] = None,
        detail: Optional[str] = None,
    ) -> None:
        """Move the order to `target`, stamping the matching timestamp."""
        self._validate_date(at, "at")
        validate_transition(self.status, target)
        self.status = target
        if target is OrderStatus.ACCEPTED:
            self.accepted_at = at
        elif target is OrderStatus.PENDING:
            self.pending_at = at
        elif target is OrderStatus.CANCELLED:
            self.cancelled_at = at
            if reason is not None:
                self.reject_reason = reason
            if detail is not None:
                self.reject_detail = detail
        elif target is OrderStatus.REJECTED:
            self.rejected_at = at
            self.reject_reason = reason or RejectReason.OTHER
            self.reject_detail = detail or ""
        elif target is OrderStatus.FILLED:
            self.filled_at = at
        elif target is OrderStatus.PARTIALLY_FILLED:
            self.partially_filled_at = at

    def record_fill(
        self,
        fill_id: str,
        quantity: int,
        price: Decimal,
        at: str,
    ) -> None:
        """Record a single fill and update running aggregates.

        v0.1 always moves orders through ACCEPTED → PENDING before any
        fill arrives, so the ACCEPTED branch in the status guard was
        unreachable in practice (task 16).
        """
        if self.status not in (
            OrderStatus.PENDING,
            OrderStatus.PARTIALLY_FILLED,
        ):
            raise ValueError(f"cannot record a fill for {self.status.name} order")
        if not fill_id or fill_id in self.fill_ids:
            raise ValueError(f"fill_id must be new and non-empty, got {fill_id!r}")
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise ValueError(
                f"fill quantity must be int, got {type(quantity).__name__}"
            )
        if quantity <= 0:
            raise ValueError(f"fill quantity must be positive, got {quantity}")
        if not isinstance(price, Decimal):
            raise ValueError(f"fill price must be Decimal, got {type(price).__name__}")
        if price <= 0:
            raise ValueError(f"fill price must be positive, got {price}")
        self._validate_date(at, "at")
        new_filled = self.filled_quantity + quantity
        if new_filled > self.quantity:
            raise ValueError(
                f"fill overflow: {self.filled_quantity}+{quantity} > {self.quantity}"
            )
        if self.avg_fill_price is None:
            self.avg_fill_price = price.quantize(PRICE_QUANT)
        else:
            total = self.avg_fill_price * Decimal(
                self.filled_quantity
            ) + price * Decimal(quantity)
            self.avg_fill_price = (total / Decimal(new_filled)).quantize(PRICE_QUANT)
        self.filled_quantity = new_filled
        self.fill_ids.append(fill_id)
        if self.filled_quantity == self.quantity:
            self.transition(OrderStatus.FILLED, at=at)
        elif self.status is not OrderStatus.PARTIALLY_FILLED:
            self.transition(OrderStatus.PARTIALLY_FILLED, at=at)

    def remaining(self) -> int:
        return self.quantity - self.filled_quantity

    def is_terminal(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        )
