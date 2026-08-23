"""hqbacktest engine layer.

Re-exports the public API of the engine package:

    from hqbacktest.engine import BacktestConfig, BacktestEngine, Strategy
"""

from .config import BacktestConfig
from .context import Context
from .engine import BacktestEngine
from .errors import (
    ConfigurationError,
    DataPortalNotConfigured,
    EngineError,
    RunFailed,
    StrategyLifecycleError,
)
from .events import EngineEvent, EventLog
from .iterator import TradingDayIterator
from .result import BacktestResult
from .scheduler import (
    PHASE_SCHEDULE,
    PhaseSchedule,
    build_view,
    previous_trading_day,
    run_day,
)
from .strategy import NullStrategy, Strategy

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "Context",
    "ConfigurationError",
    "DataPortalNotConfigured",
    "EngineError",
    "EngineEvent",
    "EventLog",
    "NullStrategy",
    "PHASE_SCHEDULE",
    "PhaseSchedule",
    "RunFailed",
    "Strategy",
    "StrategyLifecycleError",
    "TradingDayIterator",
    "build_view",
    "previous_trading_day",
    "run_day",
]
