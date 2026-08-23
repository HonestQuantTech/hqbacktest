"""BacktestConfig: minimum user-facing configuration for the engine.

Task 5 added date + cash + source fields. Task 8 added pluggable
`TradingRuleSet` and `CostModel` (explicit fees). Task 9 added the
`adjustment_policy` field with strict "none"-only validation.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ..data.errors import InvalidDataError
from ..data.hqdata_portal import DEFAULT_DATA_ROOT
from ..data.validators import validate_yyyymmdd
from .corporate_actions import V01_ADJUSTMENT_POLICY
from .cost_model import CostModel, DefaultCostModel
from .errors import ConfigurationError
from .metrics import MetricsConfig
from .rule_set import DEFAULT_V01_RULES, TradingRuleSet

# Re-exported for convenience; the single definition lives in
# `corporate_actions.py` (imported above). The corresponding enum lives in
# `domain.enums.AdjustmentPolicy`.
__all__ = ["BacktestConfig", "V01_ADJUSTMENT_POLICY"]


@dataclass
class BacktestConfig:
    """Static description of a backtest run.

    `data_root` and `source` together locate the CSV snapshot; if `data_root`
    is left at the default, the engine uses the hqdata CLI's default root.
    `source` may be empty here; the engine enforces the requirement at run
    time (so configuration validation errors surface as `ConfigurationError`
    rather than `DataPortalNotConfigured`).

    `rule_set` and `cost_model` are pluggable; the engine constructs them
    from defaults if not supplied. Rates in `DefaultCostModel` are explicit
    (TODO task 8 verification: no hidden constants).

    `adjustment_policy` MUST be "none" in v0.1; any other value is
    rejected at validation time. The corresponding enum lives in
    `domain.enums.AdjustmentPolicy`; future values (e.g.
    `factor_total_return`) are reserved but not implemented.
    """

    start_date: str
    end_date: str
    initial_cash: Decimal
    source: str = ""
    data_root: str = DEFAULT_DATA_ROOT
    rule_set: TradingRuleSet = field(
        default_factory=lambda: TradingRuleSet(DEFAULT_V01_RULES)
    )
    cost_model: CostModel = field(default_factory=lambda: DefaultCostModel())
    adjustment_policy: str = V01_ADJUSTMENT_POLICY
    metrics: MetricsConfig = field(default_factory=MetricsConfig)

    def __post_init__(self) -> None:
        try:
            validate_yyyymmdd(self.start_date, name="start_date")
            validate_yyyymmdd(self.end_date, name="end_date")
        except InvalidDataError as exc:
            raise ConfigurationError(str(exc)) from exc
        if self.start_date > self.end_date:
            raise ConfigurationError(
                f"start_date {self.start_date} is after end_date {self.end_date}"
            )
        if not isinstance(self.initial_cash, Decimal):
            # Contract rule 5: float is forbidden (binary rounding leaks).
            if isinstance(self.initial_cash, float):
                raise ConfigurationError(
                    "initial_cash must be Decimal/str/int; float is forbidden"
                )
            try:
                self.initial_cash = Decimal(str(self.initial_cash))
            except Exception as exc:
                raise ConfigurationError(
                    f"initial_cash must be Decimal/str/int, got "
                    f"{type(self.initial_cash).__name__}: {exc}"
                ) from exc
        if self.initial_cash < 0:
            raise ConfigurationError("initial_cash must be non-negative")
        if not isinstance(self.rule_set, TradingRuleSet):
            raise ConfigurationError("rule_set must be a TradingRuleSet instance")
        if not isinstance(self.cost_model, CostModel):
            raise ConfigurationError("cost_model must satisfy the CostModel protocol")
        if not isinstance(self.metrics, MetricsConfig):
            raise ConfigurationError("metrics must be a MetricsConfig instance")
        # Adjustment policy: v0.1 only accepts "none". Any other value is
        # rejected with an explicit reason (TODO task 9 verification:
        # "配置只接受 AdjustmentPolicy=none, 其他值均带明确原因拒绝").
        if not isinstance(self.adjustment_policy, str):
            raise ConfigurationError("adjustment_policy must be a string")
        if self.adjustment_policy != V01_ADJUSTMENT_POLICY:
            raise ConfigurationError(
                f"v0.1 only supports adjustment_policy={V01_ADJUSTMENT_POLICY!r}; "
                f"got {self.adjustment_policy!r}. 'factor_total_return' requires "
                "a CorporateActionProvider implementation with full accounting "
                "semantics and regression tests; not in v0.1."
            )
