"""Engine-level errors.

All engine/runtime errors derive from `EngineError` so callers can catch the
family in one place. The error types follow contract §6 rules 12 (no silent
half-day state) and 10 (every event has a reason).
"""


class EngineError(Exception):
    """Base class for every error raised by the engine layer."""


class ConfigurationError(EngineError):
    """Invalid `BacktestConfig` or unsupported combination of arguments."""


class RunFailed(EngineError):
    """A backtest run aborted before completing the trading day loop.

    Carries the failing date, phase and the original exception for the audit
    trail (contract rule 10 / 12).
    """

    def __init__(
        self,
        date: str,
        phase: str,
        original: BaseException,
    ) -> None:
        super().__init__(
            f"backtest aborted on {date} ({phase}): {type(original).__name__}: {original}"
        )
        self.date = date
        self.phase = phase
        self.original = original


class DataPortalNotConfigured(EngineError):
    """Engine.run() called without a portal and no source on the config."""


class StrategyLifecycleError(EngineError):
    """Strategy misuse detected at the engine boundary (e.g. double-init)."""


class NotInitializedError(StrategyLifecycleError):
    """A strategy callback ran before `initialize()` was called."""


class DoubleInitializationError(StrategyLifecycleError):
    """`initialize()` was called more than once in the same run."""


class CallbackAfterRunError(StrategyLifecycleError):
    """A strategy method or context accessor was used after `run()` returned."""


class UnsupportedOrderTypeError(StrategyLifecycleError):
    """The strategy tried to place an order type that v0.1 does not support."""


class NoPriceForOrderError(StrategyLifecycleError):
    """An order helper could not determine a current price for the symbol."""
