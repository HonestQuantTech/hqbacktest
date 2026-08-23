"""BacktestEngine: minimum viable engine for task 5.

Responsibilities in this task:
    - Build a `MarketDataPortal` from `BacktestConfig` (default: CSV portal).
    - Iterate the trading days returned by the portal (no natural-day loop).
    - Dispatch the five phases per day in contract §4 order.
    - Maintain a single `EventLog` covering the whole run.
    - Build a `BacktestResult` with the configuration snapshot, the log and
      the list of dates actually exercised.

Out of scope (deferred to tasks 6/7/8):
    - Order lifecycle and matching (only data + clock here).
    - Cost model and fees.
    - Settlement, snapshots, performance metrics.
"""

from dataclasses import asdict
from typing import Optional

from ..data.data_view import DataView
from ..data.hqdata_portal import HqDataCsvPortal, resolve_source_location
from ..data.portal import MarketDataPortal
from ..domain.enums import EventType
from ..domain.portfolio import Portfolio
from .config import BacktestConfig
from .context import Context
from .errors import (
    ConfigurationError,
    DataPortalNotConfigured,
    RunFailed,
    StrategyLifecycleError,
)
from .events import EngineEvent, EventLog
from .iterator import TradingDayIterator
from .result import BacktestResult
from .scheduler import run_day
from .strategy import NullStrategy, Strategy


class BacktestEngine:
    """Drive the daily event loop for a backtest run."""

    def __init__(
        self,
        config: BacktestConfig,
        strategy: Optional[Strategy] = None,
        portal: Optional[MarketDataPortal] = None,
    ) -> None:
        if not isinstance(config, BacktestConfig):
            raise ConfigurationError(
                f"config must be a BacktestConfig, got {type(config).__name__}"
            )
        self._config = config
        self._strategy = strategy if strategy is not None else NullStrategy()
        self._portal = portal
        self._event_log = EventLog()
        self._portfolio = Portfolio(initial_cash=config.initial_cash)
        self._initialized = False

    # ------------------------------------------------------------------ #
    # Accessors
    # ------------------------------------------------------------------ #

    @property
    def config(self) -> BacktestConfig:
        return self._config

    @property
    def portal(self) -> MarketDataPortal:
        if self._portal is None:
            self._portal = self._build_default_portal()
        return self._portal

    @property
    def event_log(self) -> EventLog:
        return self._event_log

    @property
    def portfolio(self) -> Portfolio:
        return self._portfolio

    # ------------------------------------------------------------------ #
    # Run
    # ------------------------------------------------------------------ #

    def run(self) -> BacktestResult:
        """Execute the backtest loop; return the result snapshot.

        `strategy.initialize(context)` is fired exactly once here, before the
        first trading day (contract §4: it is a run-level lifecycle callback,
        not a daily one). `run()` itself may only be called once per engine:
        the portfolio and event log are per-run state.
        """
        if self._initialized:
            raise StrategyLifecycleError(
                "BacktestEngine.run() may only be called once per engine"
            )
        self._initialized = True
        portal = self.portal
        result = BacktestResult(
            config_snapshot=asdict(self._config),
            event_log=self._event_log,
        )
        iterator = TradingDayIterator(
            portal=portal,
            start=self._config.start_date,
            end=self._config.end_date,
        )
        context = Context(
            current_date="",
            portfolio=self._portfolio,
            event_log=self._event_log,
        )
        self._initialize_strategy(context)

        for today in iterator:
            self._run_day_safely(today, portal, context)
            result.trading_days.append(today)
        return result

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _build_default_portal(self) -> MarketDataPortal:
        if not self._config.source:
            raise DataPortalNotConfigured(
                "BacktestConfig.source is required when no portal is supplied"
            )
        data_root, source_name = resolve_source_location(
            self._config.source, default_data_root=self._config.data_root
        )
        return HqDataCsvPortal(source=source_name, data_root=data_root)

    def _initialize_strategy(self, context: Context) -> None:
        """Fire `strategy.initialize` once; failures abort the run."""
        context.phase = EventType.SESSION_START
        try:
            self._strategy.initialize(context)
        except Exception as exc:
            self._event_log.record(
                EngineEvent(
                    date=self._config.start_date,
                    phase=EventType.RUN_FAILED,
                    error=type(exc).__name__,
                    detail=f"initialize: {exc}",
                )
            )
            raise RunFailed(self._config.start_date, "INITIALIZE", exc) from exc
        finally:
            context.phase = None

    def _run_day_safely(
        self, today: str, portal: MarketDataPortal, context: Context
    ) -> None:
        try:
            run_day(
                today=today,
                portal=portal,
                strategy=self._strategy,
                context=context,
                log=self._event_log,
            )
        except (RunFailed, StrategyLifecycleError):
            raise
        except Exception as exc:
            # Record the failure in the phase where it actually happened so
            # the audit trail never misattributes it; fall back to RUN_FAILED
            # only when no phase context exists.
            phase = context.phase
            phase_name = phase.name if phase is not None else "UNKNOWN"
            self._event_log.record(
                EngineEvent(
                    date=today,
                    phase=phase if phase is not None else EventType.RUN_FAILED,
                    error=type(exc).__name__,
                    detail=str(exc),
                )
            )
            raise RunFailed(today, phase_name, exc) from exc

    # ------------------------------------------------------------------ #
    # Deterministic helpers (used by tests)
    # ------------------------------------------------------------------ #

    def data_view(self, today: str) -> DataView:
        """Build a `DataView` snapshot as the engine would at `BAR_CLOSE(today)`."""
        return DataView(portal=self.portal, visible_through=today)
