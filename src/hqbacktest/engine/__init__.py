"""hqbacktest engine layer.

Re-exports the public API of the engine package:

    from hqbacktest.engine import BacktestConfig, BacktestEngine, Strategy
"""

from .broker import SimulatedBroker
from .config import V01_ADJUSTMENT_POLICY, BacktestConfig
from .context import Context
from .corporate_actions import (
    DIAGNOSTIC_KINDS,
    FACTOR_TOTAL_RETURN_ADMISSION_CRITERIA,
    REQUIRED_CORPORATE_ACTION_FIELDS,
    CorporateAction,
    CorporateActionProvider,
    FactorDiagnostic,
    FactorDiagnosticCollector,
    analyze_factor_series,
)
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
from .metrics import EquityPoint, MetricsConfig, PerformanceMetrics, compute_metrics
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
    "MetricsConfig",
    "PHASE_SCHEDULE",
    "REQUIRED_CORPORATE_ACTION_FIELDS",
    "V01_ADJUSTMENT_POLICY",
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "BaseStrategy",
    "CallbackAfterRunError",
    "ConfigurationError",
    "Context",
    "CorporateAction",
    "CorporateActionProvider",
    "Cost",
    "CostModel",
    "DataPortalNotConfigured",
    "DefaultCostModel",
    "DoubleInitializationError",
    "EngineError",
    "EngineEvent",
    "EquityPoint",
    "EventLog",
    "FactorDiagnostic",
    "FactorDiagnosticCollector",
    "DIAGNOSTIC_KINDS",
    "InsufficientCashRule",
    "InvalidPriceRule",
    "LongOnlyRule",
    "LotSizeRule",
    "NoPriceForOrderError",
    "NonTradingDayRule",
    "NotInitializedError",
    "NullStrategy",
    "PerformanceMetrics",
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
    "analyze_factor_series",
    "TradingRuleSet",
    "UnsupportedOrderTypeError",
    "build_view",
    "compute_metrics",
    "normalize_percent",
    "previous_trading_day",
    "quantity_from_value",
    "run_day",
    "side_from_quantity",
    "signed_diff_to_lots",
    "target_quantity_for_value",
    "target_value_for_percent",
]
