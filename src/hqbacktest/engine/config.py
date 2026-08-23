"""BacktestConfig: minimum user-facing configuration for the engine.

Only the fields strictly required by task 5 are defined here. Order-side
options (cost model, adjustment policy, etc.) are added in later tasks.
"""

from dataclasses import dataclass
from decimal import Decimal

from ..data.errors import InvalidDataError
from ..data.hqdata_portal import DEFAULT_DATA_ROOT
from ..data.validators import validate_yyyymmdd
from .errors import ConfigurationError


@dataclass
class BacktestConfig:
    """Static description of a backtest run.

    `data_root` and `source` together locate the CSV snapshot; if `data_root`
    is left at the default, the engine uses the hqdata CLI's default root.
    `source` may be empty here; the engine enforces the requirement at run
    time (so configuration validation errors surface as `ConfigurationError`
    rather than `DataPortalNotConfigured`).
    """

    start_date: str
    end_date: str
    initial_cash: Decimal
    source: str = ""
    data_root: str = DEFAULT_DATA_ROOT

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
