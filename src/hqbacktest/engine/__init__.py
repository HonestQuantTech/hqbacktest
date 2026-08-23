"""hqbacktest engine layer.

Re-exports the public API of the engine package:

    from hqbacktest.engine import BacktestConfig, BacktestEngine, Strategy
"""

from .broker import SimulatedBroker
from .config import BacktestConfig
from .context import Context
from .cost_model import Cost, CostModel, DefaultCostModel
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
from .rule_set import (
    DEFAULT_V01_RULES,
    InsufficientCashRule,
    InvalidPriceRule,
    LotSizeRule,
    LongOnlyRule,
    NonTradingDayRule,
    Rule,
    RuleCheckContext,
    RuleResult,
    T1SellableRule,
    TradingRuleSet,
)
from .scheduler import (
    PHASE_SCHEDULE,
    PhaseSchedule,
    build_view,
    previous_trading_day,
    run_day,
)
from .strategy import BaseStrategy, NullStrategy, Strategy

__all__ = [
    "DEFAULT_V01_RULES",
    "PHASE_SCHEDULE",
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "BaseStrategy",
    "CallbackAfterRunError",
    "ConfigurationError",
    "Context",
    "Cost",
    "CostModel",
    "DataPortalNotConfigured",
    "DefaultCostModel",
    (
        "DefaultTradingRuleSet" if False else "DEFAULT_V01_RULES"
    ),  # backward-compat alias below
    "DoubleInitializationError",
    "EngineError",
    "EngineEvent",
    "EventLog",
    "InsufficientCashRule",
    "InvalidPriceRule",
    "LongOnlyRule",
    "LotSizeRule",
    "NoPriceForOrderError",
    "NonTradingDayRule",
    "NotInitializedError",
    "NullStrategy",
    "PhaseSchedule",
    "Rule",
    "RuleCheckContext",
    "RuleResult",
    "RunFailed",
    "SimulatedBroker",
    "Strategy",
    "StrategyLifecycleError",
    "T1SellableRule",
    "TradingDayIterator",
    "TradingRuleSet",
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
