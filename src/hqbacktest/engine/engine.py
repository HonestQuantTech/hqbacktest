"""BacktestEngine: event clock plus controlled strategy context.

Responsibilities after task 10:
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
      collector in the result.
    - Build the per-day `equity_curve` from close prices and snapshot
      positions / costs / orders / fills into typed tables on the result.
    - Compute `PerformanceMetrics` from the equity curve and fills.
    - Maintain a single `EventLog` covering the whole run.

Out of scope (deferred to task 12+):
    - Interactive / HTML reports.
"""

from dataclasses import asdict
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..data.data_view import DataView
from ..data.errors import MissingDataError
from ..data.hqdata_portal import HqDataCsvPortal, resolve_source_location
from ..data.portal import MarketDataPortal
from ..domain.enums import EventType, OrderStatus, RejectReason
from ..domain.errors import InsufficientCashError, InsufficientSharesError
from ..domain.fill import Fill
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
from .metrics import EquityPoint, compute_metrics
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
        self._fills: List[Fill] = []
        self._equity_curve: List[EquityPoint] = []
        # Every order the engine has consumed (insertion-ordered), so the
        # orders table is built from real Order objects — never scraped
        # from event-log strings.
        self._orders: Dict[str, Order] = {}
        # Per-day per-symbol position rows, snapshotted at day end with
        # that day's close price.
        self._positions_rows: List[Dict[str, Any]] = []
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
        # Build the result tables only after the day loop completed
        # successfully. If any day raised `RunFailed` we re-raised above, so
        # this code path is reached only on a clean run.
        self._populate_result(result)
        # Only publish the result after a fully successful run: a failed run
        # must not leave a half-populated BacktestResult on the engine.
        self._result = result
        return result

    def _populate_result(self, result: "BacktestResult") -> None:
        """Fill in equity_curve, orders/fills/positions/costs tables, metrics."""
        result.equity_curve = list(self._equity_curve)
        result.fills_table = [self._fill_row(f) for f in self._fills]
        result.costs_table = [self._cost_row(f) for f in self._fills]
        result.orders_table = [self._order_row(o) for o in self._orders.values()]
        result.positions_table = list(self._positions_rows)
        result.data_version = asdict(self.portal.data_version())
        result.metrics = compute_metrics(
            equity_curve=result.equity_curve,
            fills=self._fills,
            initial_cash=self._config.initial_cash,
            config=self._config.metrics,
        )
        result.factor_diagnostics = list(self._factor_diagnostics.all())

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
            self._snapshot_equity(today, portal)
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

    def _fill_row(self, fill: Fill) -> Dict[str, Any]:
        return {
            "fill_id": fill.fill_id,
            "order_id": fill.order_id,
            "symbol": fill.symbol,
            "side": fill.side.name,
            "quantity": str(fill.quantity),
            "price": str(fill.price),
            "amount": str(fill.amount),
            "commission": str(fill.commission),
            "stamp_tax": str(fill.stamp_tax),
            "other_fee": str(fill.other_fee),
            "filled_at": fill.filled_at,
            "session": fill.session.name,
        }

    def _cost_row(self, fill: Fill) -> Dict[str, Any]:
        return {
            "date": fill.filled_at,
            "fill_id": fill.fill_id,
            "order_id": fill.order_id,
            "symbol": fill.symbol,
            "side": fill.side.name,
            "quantity": str(fill.quantity),
            "gross": str(abs(fill.amount)),
            "commission": str(fill.commission),
            "stamp_tax": str(fill.stamp_tax),
            "other_fee": str(fill.other_fee),
            "net": str(fill.net_amount()),
        }

    def _order_row(self, order: Order) -> Dict[str, Any]:
        """One orders-table row, built from the real Order object."""
        fills = [f for f in self._fills if f.order_id == order.order_id]
        commission_total = sum((f.commission for f in fills), Decimal("0"))
        return {
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": order.side.name,
            "quantity": str(order.quantity),
            "order_type": order.order_type.name,
            "status": order.status.name,
            "created_at": order.created_at,
            "created_session": order.created_session.name,
            "filled_at": order.filled_at or "",
            "avg_fill_price": (
                str(order.avg_fill_price) if order.avg_fill_price is not None else ""
            ),
            "commission_total": str(commission_total) if fills else "",
            "reject_reason": (
                order.reject_reason.name if order.reject_reason is not None else ""
            ),
            "reject_detail": order.reject_detail or "",
        }

    def _snapshot_equity(self, today: str, portal: MarketDataPortal) -> None:
        """Record one EquityPoint using today's close for market value.

        Contract §4: day-end valuation uses D's valid unadjusted close. If a
        HELD symbol has no valid close (missing bar, or close <= 0), the run
        FAILS with a DATA_ERROR event — v0.1 never silently skips valuation,
        never uses previous closes, and never values holdings at zero.
        """
        prices: Dict[str, Decimal] = {}
        for symbol, position in self._portfolio.positions.items():
            if position.quantity == 0:
                continue
            close = self._close_price_or_none(portal, symbol, today)
            if close is None:
                self._event_log.record(
                    EngineEvent(
                        date=today,
                        phase=EventType.DATA_ERROR,
                        error="MissingDataError",
                        detail=(
                            f"no valid close for held symbol {symbol} on "
                            f"{today}; valuation aborted"
                        ),
                    )
                )
                raise RunFailed(
                    today,
                    "AFTER_TRADING_END",
                    MissingDataError(
                        "close", f"no valid close for held symbol {symbol} on {today}"
                    ),
                )
            prices[symbol] = close
        market_value = self._portfolio.market_value(prices)
        total_equity = self._portfolio.cash + market_value
        prev_total = self._equity_curve[-1].total_equity if self._equity_curve else None
        if prev_total is None or prev_total == 0:
            daily_return = Decimal("0")
        else:
            daily_return = total_equity / prev_total - Decimal("1")
        if self._equity_curve:
            peak = max(pt.total_equity for pt in self._equity_curve)
            drawdown = (
                max(Decimal("0"), (peak - total_equity) / peak)
                if peak > 0
                else Decimal("0")
            )
        else:
            drawdown = Decimal("0")
        self._equity_curve.append(
            EquityPoint(
                date=today,
                cash=self._portfolio.cash,
                market_value=market_value,
                total_equity=total_equity,
                daily_return=daily_return,
                drawdown=drawdown,
            )
        )
        # Per-day position snapshot with today's actual close prices.
        for symbol, position in self._portfolio.positions.items():
            if position.quantity == 0:
                continue
            price = prices[symbol]
            self._positions_rows.append(
                {
                    "date": today,
                    "symbol": symbol,
                    "quantity": str(position.quantity),
                    "sellable_quantity": str(position.sellable_quantity),
                    "avg_cost": str(position.avg_cost),
                    "market_price": str(price),
                    "market_value": str(position.market_value(price)),
                }
            )

    @staticmethod
    def _close_price_or_none(
        portal: MarketDataPortal, symbol: str, today: str
    ) -> Optional[Decimal]:
        """Today's close for `symbol`, or None when missing/invalid.

        Only `MissingDataError` maps to None; corrupt data or I/O errors
        propagate and abort the run via the caller's RunFailed wrapping.
        """
        try:
            bars = portal.get_bars(symbol, today, today)
        except MissingDataError:
            return None
        if not bars:
            return None
        close = bars[0].close
        if close is None or close <= 0:
            return None
        return close

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
        for order in pending:
            self._orders[order.order_id] = order
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
            self._fills.append(fill)
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
            self._orders[order.order_id] = order
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
                    error=RejectReason.BACKTEST_ENDED.name,
                    detail="no OPEN_MATCH left in the backtest window",
                )
            )

    # ------------------------------------------------------------------ #
    # Deterministic helpers (used by tests)
    # ------------------------------------------------------------------ #

    def data_view(self, today: str) -> DataView:
        """Build a `DataView` snapshot as the engine would at `BAR_CLOSE(today)`."""
        return DataView(portal=self.portal, visible_through=today)
