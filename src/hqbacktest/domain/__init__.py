"""hqbacktest domain layer.

Re-exports the public API of the domain package so callers can write:

    from hqbacktest.domain import Order, Fill, Portfolio, ...
"""

from .adjustment import CorporateActionAdjustment
from .bar import Bar
from .enums import EventType, OrderStatus, OrderType, PriceMode, RejectReason, Side
from .fill import Fill
from .money import (
    CASH_QUANT,
    LOT_SIZE,
    PRICE_QUANT,
    MoneyError,
    cash_for_trade,
    is_non_negative,
    is_positive,
    quantize_cash,
    quantize_price,
    round_lot,
    to_decimal,
)
from .order import Order
from .portfolio import Portfolio
from .position import Position, empty_positions
from .serialization import dump_json, dump_jsonl, to_jsonable
from .snapshot import AccountSnapshot, PositionSnapshot
from .state_machine import (
    TERMINAL_STATUSES,
    VALID_TRANSITIONS,
    IllegalStateTransition,
    is_terminal,
    validate_transition,
)

__all__ = [
    "AccountSnapshot",
    "Bar",
    "CASH_QUANT",
    "CorporateActionAdjustment",
    "EventType",
    "Fill",
    "IllegalStateTransition",
    "LOT_SIZE",
    "MoneyError",
    "Order",
    "OrderStatus",
    "OrderType",
    "PRICE_QUANT",
    "Portfolio",
    "Position",
    "PositionSnapshot",
    "PriceMode",
    "RejectReason",
    "Side",
    "TERMINAL_STATUSES",
    "VALID_TRANSITIONS",
    "cash_for_trade",
    "dump_json",
    "dump_jsonl",
    "empty_positions",
    "is_non_negative",
    "is_positive",
    "is_terminal",
    "quantize_cash",
    "quantize_price",
    "round_lot",
    "to_decimal",
    "to_jsonable",
    "validate_transition",
]
