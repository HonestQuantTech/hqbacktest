"""hqbacktest - A-share quantitative strategy backtest and trading simulation engine."""

from hqbacktest.domain import (
    AccountSnapshot,
    Bar,
    CorporateActionAdjustment,
    EventType,
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    PositionSnapshot,
    PriceMode,
    RejectReason,
    Side,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "AccountSnapshot",
    "Bar",
    "CorporateActionAdjustment",
    "EventType",
    "Fill",
    "Order",
    "OrderStatus",
    "OrderType",
    "Portfolio",
    "Position",
    "PositionSnapshot",
    "PriceMode",
    "RejectReason",
    "Side",
]
