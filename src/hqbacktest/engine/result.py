"""BacktestResult: audit-trail summary returned by `BacktestEngine.run()`.

Tasks 5 / 7 / 9 progressively add fields. v0.1 includes:
    * `config_snapshot`        - serialised `BacktestConfig` (asdict).
    * `event_log`              - the run's `EventLog`.
    * `trading_days`           - dates actually iterated.
    * `adjustment_policy`      - the policy that was applied (always "none"
                                 in v0.1; recorded for the audit trail).
    * `factor_diagnostics`     - factor-quality observations; empty in v0.1
                                 because factor_total_return is disabled.

Failed runs never produce a `BacktestResult`: `BacktestEngine.run()`
aborts with `RunFailed` instead.
"""

from dataclasses import dataclass, field
from typing import List

from .corporate_actions import V01_ADJUSTMENT_POLICY, FactorDiagnostic
from .events import EventLog


@dataclass
class BacktestResult:
    """What `BacktestEngine.run()` returns after a successful run."""

    config_snapshot: dict
    event_log: EventLog
    trading_days: List[str] = field(default_factory=list)
    adjustment_policy: str = V01_ADJUSTMENT_POLICY
    factor_diagnostics: List[FactorDiagnostic] = field(default_factory=list)
