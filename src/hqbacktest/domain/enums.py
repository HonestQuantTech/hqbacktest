"""Domain enums for hqbacktest.

All enums are closed in v0.1: any value not listed here must be rejected by the
engine rather than silently coerced. See docs/design/mvp-contract.md §6.
"""

from enum import Enum


class Side(Enum):
    """Order side."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    """Order type.

    v0.1 only accepts MARKET. Other values are intentionally absent so that
    submitting them raises UnsupportedOrderType (contract rule 7).
    """

    MARKET = "MARKET"


class OrderStatus(Enum):
    """Order lifecycle states.

    Terminal states: FILLED, CANCELLED, REJECTED.
    """

    NEW = "NEW"
    ACCEPTED = "ACCEPTED"
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class RejectReason(Enum):
    """Why an order was rejected or cancelled without fill.

    Each rejection must carry one of these reasons plus an optional free-form
    detail string for diagnostics.
    """

    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    INSUFFICIENT_SHARES = "INSUFFICIENT_SHARES"
    INVALID_PRICE = "INVALID_PRICE"
    NO_OPEN_PRICE = "NO_OPEN_PRICE"
    SUSPENDED = "SUSPENDED"
    UNKNOWN_SYMBOL = "UNKNOWN_SYMBOL"
    NOT_TRADABLE = "NOT_TRADABLE"
    UNSUPPORTED_ORDER_TYPE = "UNSUPPORTED_ORDER_TYPE"
    MISSING_DATA = "MISSING_DATA"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    BACKTEST_ENDED = "BACKTEST_ENDED"
    OTHER = "OTHER"


class EventType(Enum):
    """Engine and strategy event categories.

    Phase events follow contract §4. Order events describe lifecycle changes.
    """

    SESSION_START = "SESSION_START"
    BEFORE_TRADING_START = "BEFORE_TRADING_START"
    OPEN_MATCH = "OPEN_MATCH"
    BAR_CLOSE = "BAR_CLOSE"
    AFTER_TRADING_END = "AFTER_TRADING_END"

    ORDER_CREATED = "ORDER_CREATED"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    ORDER_PENDING = "ORDER_PENDING"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    ORDER_FILLED = "ORDER_FILLED"

    DATA_ERROR = "DATA_ERROR"
    RUN_FAILED = "RUN_FAILED"


class PriceMode(Enum):
    """Price reference used by the engine.

    v0.1 uses OPEN for market orders during OPEN_MATCH.
    """

    OPEN = "OPEN"
