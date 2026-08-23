"""BacktestEngine: event clock plus controlled strategy context.

Responsibilities after task 9:
    - Build a `MarketDataPortal` from `BacktestConfig` (default: CSV portal).
    - Iterate the trading days returned by the portal (no natural-day loop).
    - Dispatch the five phases per day in contract §4 order.
    - Fire `strategy.initialize` exactly once and lock the universe after it.
    - At the `OPEN_MATCH` phase, hand pending orders to `SimulatedBroker`
      with the configured `TradingRuleSet` and `CostModel`, apply the
      resulting fills to the portfolio, and mark rejections on the orders
      (with rule name / reason / detail).
    - After the last trading day, cancel still-pending orders with reason
      `BACKTEST_ENDED` (contract §4); the run is never extended to fill them.
    - Record the active adjustment policy and the factor-diagnostics
      collector in the result (task 9). v0.1 only supports "none" so the
      policy marker is informational; factor diagnostics are dormant.
    - Maintain a single `EventLog` covering the whole run.
    - Build a `BacktestResult` with the configuration snapshot, the log,
      the trading days, the policy, and the diagnostics.

Out of scope (deferred to task 10):
    - Performance metrics, snapshots, persistence.
"""

from dataclasses import asdict
from typing import List, Optional

from ..data.data_view import DataView
from ..data.hqdata_portal import HqDataCsvPortal, resolve_source_location
from ..data.portal import MarketDataPortal
from ..domain.enums import EventType, OrderStatus, RejectReason
from ..domain.errors import InsufficientCashError, InsufficientSharesError
from ..domain.order import Order
from ..domain.portfolio import Portfolio
from .broker import SimulatedBroker
from .config import BacktestConfig
from .context import Context
from .corporate_actions import (
    FactorDiagnosticCollector,
    V01_ADJUSTMENT_POLICY,
)
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
        broker: Optional[SimulatedBroker] = None,
    ) -> None:
        if not isinstance(config, BacktestConfig):
            raise ConfigurationError(
                f"config must be a BacktestConfig, got {type(config).__name__}"
            )
        self._config = config
        self._strategy = strategy if strategy is not None else NullStrategy()
        self._portal = portal
        # Task 8: broker must share the configured CostModel.
        cost = config.cost_model
        self._broker = (
            broker if broker is not None else SimulatedBroker(cost_model=cost)
        )
        self._event_log = EventLog()
        self._portfolio = Portfolio(initial_cash=config.initial_cash)
        self._factor_diagnostics = FactorDiagnosticCollector()
        self._initialized = False
        self._result: Optional[BacktestResult] = None

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
    def broker(self) -> SimulatedBroker:
        return self._broker

    @property
    def event_log(self) -> EventLog:
        return self._event_log

    @property
    def portfolio(self) -> Portfolio:
        return self._portfolio

    @property
    def factor_diagnostics(self) -> FactorDiagnosticCollector:
        return self._factor_diagnostics

    @property
    def result(self) -> Optional[BacktestResult]:
        """Most recent run's result, or None if `run()` has not been called."""
        return self._result

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
            adjustment_policy=self._config.adjustment_policy,
        )
        iterator = TradingDayIterator(
            portal=portal,
            start=self._config.start_date,
            end=self._config.end_date,
        )
        # No DataView is attached yet: `initialize` / `SESSION_START` have no
        # market data access (contract §4). The scheduler publishes a
        # properly bounded view per phase via `context._set_data_view`.
        context = Context(
            current_date=self._config.start_date,
            portfolio=self._portfolio,
            event_log=self._event_log,
        )
        try:
            self._initialize_strategy(context)
            for today in iterator:
                self._run_day_safely(today, portal, context)
                result.trading_days.append(today)
            self._cancel_leftover_orders(context, result.trading_days)
        finally:
            context._mark_run_finished()
        result.factor_diagnostics = list(self._factor_diagnostics.all())
        # Only publish the result after a fully successful run: a failed run
        # must not leave a half-populated BacktestResult on the engine.
        self._result = result
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
        """Fire `strategy.initialize` once; failures abort the run.

        The context is marked initialized first so strategies can call
        `set_universe` and read-only accessors from inside the override;
        ordering and market data remain unavailable (contract §4: no data
        and no orders during SESSION_START). The universe is locked as soon
        as `initialize` returns, so later callbacks cannot redeclare it.
        """
        context._set_phase(EventType.SESSION_START)
        context._mark_initialized()
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
            context._lock_universe()
            context._set_phase(None)

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
                on_open_match=self._on_open_match,
            )
            # End-of-day settlement: roll today's buys into sellable (T+1).
            self._portfolio.settle_t1(today=today, previous_date=None)
        except RunFailed:
            raise
        except Exception as exc:
            # StrategyLifecycleError raised inside a callback also lands here:
            # contract rule 12 requires the date / phase / original exception
            # to travel together, so run-time strategy misuse aborts the run
            # as RunFailed (the original error stays on `.original`).
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

    def _sellable_for(self, symbol: str) -> int:
        pos = self._portfolio.positions.get(symbol)
        return pos.sellable_quantity if pos else 0

    def _on_open_match(self, today: str, pending: List[Order]) -> None:
        """Match pending orders at `OPEN_MATCH(today)` and apply fills.

        Each order goes through `TradingRuleSet.evaluate` first; the first
        denial short-circuits with a typed rejection. Surviving orders
        receive a `Fill` whose fees come from `CostModel.compute`; the
        engine then applies each fill to the portfolio. Typed ledger
        errors (`InsufficientCashError` / `InsufficientSharesError`)
        become rejections; any other ledger error is a programming bug and
        aborts the run via `RunFailed`.
        """
        results = self._broker.match(
            pending,
            self.portal,
            today,
            self._config.rule_set,
            self._portfolio.cash,
            self._sellable_for,
        )
        for order, fill, reject_reason, reject_detail in results:
            if fill is None:
                self._reject_order(
                    order,
                    today,
                    reject_reason or RejectReason.OTHER,
                    reject_detail or "",
                )
                continue
            try:
                self._portfolio.apply_fill(fill)
            except InsufficientCashError as exc:
                self._reject_order(
                    order, today, RejectReason.INSUFFICIENT_CASH, str(exc)
                )
                continue
            except InsufficientSharesError as exc:
                self._reject_order(
                    order, today, RejectReason.INSUFFICIENT_SHARES, str(exc)
                )
                continue
            # Fill applied: record on the order (keeps fill_ids,
            # filled_quantity and avg_fill_price in sync).
            order.record_fill(fill.fill_id, fill.quantity, fill.price, at=today)
            self._event_log.record(
                EngineEvent(
                    date=today,
                    phase=EventType.ORDER_FILLED,
                    order_id=order.order_id,
                    fill_id=fill.fill_id,
                    detail=(
                        f"fill@{fill.price} qty={fill.quantity} "
                        f"comm={fill.commission} stamp={fill.stamp_tax}"
                    ),
                )
            )

    def _reject_order(
        self, order: Order, today: str, reason: RejectReason, detail: str
    ) -> None:
        """Mark an order REJECTED and append the audit-trail event."""
        order.transition(
            OrderStatus.REJECTED,
            at=today,
            reason=reason,
            detail=detail,
        )
        self._event_log.record(
            EngineEvent(
                date=today,
                phase=EventType.ORDER_REJECTED,
                order_id=order.order_id,
                error=reason.name,
                detail=detail,
            )
        )

    def _cancel_leftover_orders(
        self, context: Context, trading_days: List[str]
    ) -> None:
        """Cancel orders still pending after the last BAR_CLOSE.

        Contract §4: unfilled orders at the end of the window become
        CANCELLED with reason `BACKTEST_ENDED`; the engine never extends the
        run to fill them.
        """
        leftover = context._consume_pending_orders()
        if not leftover:
            return
        at = trading_days[-1] if trading_days else self._config.end_date
        for order in leftover:
            order.transition(
                OrderStatus.CANCELLED,
                at=at,
                reason=RejectReason.BACKTEST_ENDED,
                detail="no OPEN_MATCH left in the backtest window",
            )
            self._event_log.record(
                EngineEvent(
                    date=at,
                    phase=EventType.ORDER_CANCELLED,
                    order_id=order.order_id,
                    detail="BACKTEST_ENDED: no OPEN_MATCH left in the backtest window",
                )
            )

    # ------------------------------------------------------------------ #
    # Deterministic helpers (used by tests)
    # ------------------------------------------------------------------ #

    def data_view(self, today: str) -> DataView:
        """Build a `DataView` snapshot as the engine would at `BAR_CLOSE(today)`."""
        return DataView(portal=self.portal, visible_through=today)
