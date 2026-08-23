"""hqbacktest engine layer.

Re-exports the public API of the engine package:

    from hqbacktest.engine import BacktestConfig, BacktestEngine, Strategy
"""

from .config import BacktestConfig
from .context import Context
from .engine import BacktestEngine
from .errors import (
    CallbackAfterRunError,
    ConfigurationError,
    DataPortalNotConfigured,
    DoubleInitializationError,
    EngineError,
    NoPriceForOrderError,
    NotInitializedError,
    RunFailed,
    StrategyLifecycleError,
    UnsupportedOrderTypeError,
)
from .events import EngineEvent, EventLog
from .intents import (
    normalize_percent,
    quantity_from_value,
    side_from_quantity,
    signed_diff_to_lots,
    target_quantity_for_value,
    target_value_for_percent,
)
from .iterator import TradingDayIterator
from .result import BacktestResult
from .scheduler import (
    PHASE_SCHEDULE,
    PhaseSchedule,
    build_view,
    previous_trading_day,
    run_day,
)
from .strategy import BaseStrategy, NullStrategy, Strategy

__all__ = [
    "PHASE_SCHEDULE",
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "BaseStrategy",
    "CallbackAfterRunError",
    "ConfigurationError",
    "Context",
    "DataPortalNotConfigured",
    "DoubleInitializationError",
    "EngineError",
    "EngineEvent",
    "EventLog",
    "NoPriceForOrderError",
    "NotInitializedError",
    "NullStrategy",
    "PhaseSchedule",
    "RunFailed",
    "Strategy",
    "StrategyLifecycleError",
    "TradingDayIterator",
    "UnsupportedOrderTypeError",
    "build_view",
    "normalize_percent",
    "previous_trading_day",
    "quantity_from_value",
    "run_day",
    "side_from_quantity",
    "signed_diff_to_lots",
    "target_quantity_for_value",
    "target_value_for_percent",
]
