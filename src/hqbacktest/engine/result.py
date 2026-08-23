"""Minimal BacktestResult stub.

Task 5 only needs the audit trail and the trading dates actually exercised.
Task 10 expands this into the full `equity_curve / orders / fills / positions
/ costs / metrics` structure required by the contract.

Failed runs never produce a `BacktestResult`: `BacktestEngine.run()` aborts
with `RunFailed`, so no `failed`/`failure` fields exist here by design.
"""

from dataclasses import dataclass, field
from typing import List

from .events import EventLog


@dataclass
class BacktestResult:
    """What `BacktestEngine.run()` returns in task 5."""

    config_snapshot: dict
    event_log: EventLog
    trading_days: List[str] = field(default_factory=list)
