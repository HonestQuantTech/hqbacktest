"""Order state machine.

Defines the legal transitions for Order.status and a single entry point
(`validate_transition`) used by `Order.transition`. Illegal transitions raise
`IllegalStateTransition` instead of mutating the order, so the rest of the engine
never sees an order in a state that the contract does not list.
"""

from typing import Mapping

from .enums import OrderStatus

VALID_TRANSITIONS: Mapping[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.NEW: frozenset({OrderStatus.ACCEPTED, OrderStatus.REJECTED}),
    OrderStatus.ACCEPTED: frozenset(
        {
            OrderStatus.PENDING,
            OrderStatus.CANCELLED,
            OrderStatus.FILLED,
            OrderStatus.REJECTED,
        }
    ),
    OrderStatus.PENDING: frozenset(
        {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.PARTIALLY_FILLED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {OrderStatus.FILLED, OrderStatus.CANCELLED}
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}

TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}
)


class IllegalStateTransition(ValueError):
    """Raised when an Order is asked to move to an unreachable status."""

    def __init__(self, current: OrderStatus, target: OrderStatus) -> None:
        super().__init__(
            f"illegal order state transition: {current.name} -> {target.name}"
        )
        self.current = current
        self.target = target


def is_terminal(status: OrderStatus) -> bool:
    """Return True iff `status` admits no further transitions."""
    return status in TERMINAL_STATUSES


def validate_transition(current: OrderStatus, target: OrderStatus) -> None:
    """Raise IllegalStateTransition if `current -> target` is not allowed."""
    allowed = VALID_TRANSITIONS[current]
    if target not in allowed:
        raise IllegalStateTransition(current, target)
