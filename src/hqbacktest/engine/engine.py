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
from typing import Any, Dict, List, Optional, Tuple

from ..data.data_view import DataView
from ..data.errors import DataError, MissingDataError, SnapshotFileMissingError
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
    DEFAULT_JUMP_BAND,
    FactorDiagnostic,
    FactorDiagnosticCollector,
    V01_ADJUSTMENT_POLICY,
    analyze_factor_series,
)
from .errors import (
    ConfigurationError,
    DataPortalNotConfigured,
    RunFailed,
    StrategyLifecycleError,
)
from ..data.data_view import CURRENT_PRICE_LOOKBACK
from .events import EngineEvent, EventLog
from .iterator import TradingDayIterator
from .metrics import EquityPoint, compute_metrics
from .result import BacktestResult
from .scheduler import run_day
from .strategy import NullStrategy, Strategy


def _lookback_start_date(today: str) -> str:
    """Compute the lookback-window start date for valuation fallbacks.

    Mirrors `DataView._trading_day_lookback_start`: a generous 5-year
    window that comfortably covers `CURRENT_PRICE_LOOKBACK` trading days
    without forcing the portal to scan the full pre-start history.
    """
    yyyymmdd = int(today)
    year = yyyymmdd // 10000
    start_year = max(year - 5, 1900)
    return f"{start_year}0101"


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
        # Task 19: per-symbol cumulative factor history (sorted by
        # date) so the engine can run holdings-period factor
        # diagnostics incrementally without re-reading the portal.
        self._factor_history: Dict[str, List[Tuple[str, Decimal]]] = {}
        # Holding-period jump band (relative). A factor ratio outside
        # this band while a symbol is held is a strong dividend / split
        # signal. 0.1% matches task 19's "cannot be ignored" threshold;
        # the default `DEFAULT_JUMP_BAND` (0.5, 2.0) is reserved for
        # general factor-quality diagnostics.
        self._holding_jump_band: Tuple[Decimal, Decimal] = (
            Decimal("0.999"),
            Decimal("1.001"),
        )
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
        # Strip non-reproducible runtime objects from the snapshot
        # (`TradingRuleSet.__repr__` includes a memory address). The
        # CLI runner also reads `summary.json`, so it must be byte-stable
        # across runs.
        config_snapshot = asdict(self._config)
        config_snapshot.pop("rule_set", None)
        result = BacktestResult(
            config_snapshot=config_snapshot,
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
            # Task 19: run holdings-period factor diagnostics for
            # symbols held or traded today. Diagnostics are pure
            # observations: they never mutate cash, positions or
            # equity, and they cannot abort the run.
            self._run_factor_diagnostics(today, portal)
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

        Contract §4 + task 14 valuation semantics:
            * Preferred source: today's valid unadjusted close (D's bar).
            * Fallback: when the held symbol has no bar on `today`
              (suspended / delisted / pre-IPO), use the most recent valid
              close from the same `current_price` lookback window and
              record a `DATA_WARNING` event so the audit trail reflects
              the deviation. The fallback is bounded by
              `DataView.CURRENT_PRICE_LOOKBACK` (20) trading days.
            * If neither today's close nor any lookback close is
              available, the run FAILS with `DATA_ERROR` — v0.1 never
              values holdings at zero and never silently drops a holding.
        """
        prices: Dict[str, Decimal] = {}
        for symbol, position in self._portfolio.positions.items():
            if position.quantity == 0:
                continue
            today_price = self._close_price_or_none(portal, symbol, today)
            if today_price is not None:
                prices[symbol] = today_price
                continue
            # No bar for `symbol` today (suspended / delisted / pre-IPO).
            # Fall back to the most recent valid close within the same
            # lookback window used by `DataView.current_price` and emit a
            # `DATA_WARNING` so the audit trail reflects the deviation.
            fallback = self._lookback_price_or_none(portal, symbol, today)
            if fallback is None:
                self._event_log.record(
                    EngineEvent(
                        date=today,
                        phase=EventType.DATA_ERROR,
                        error="MissingDataError",
                        detail=(
                            f"no valid close for held symbol {symbol} on "
                            f"{today} (lookback exhausted); valuation aborted"
                        ),
                    )
                )
                raise RunFailed(
                    today,
                    "AFTER_TRADING_END",
                    MissingDataError(
                        "close",
                        f"no valid close for held symbol {symbol} on {today}",
                    ),
                )
            self._event_log.record(
                EngineEvent(
                    date=today,
                    phase=EventType.DATA_WARNING,
                    detail=(
                        f"held symbol {symbol} has no bar on {today}; "
                        f"valued at fallback close {fallback}"
                    ),
                )
            )
            prices[symbol] = fallback
        market_value = self._portfolio.market_value(prices)
        total_equity = self._portfolio.cash + market_value
        # Task 17: anchor the first day's `daily_return` and the
        # `drawdown` series to `initial_cash` (not a zero seed). A first-
        # day P&L must flow into the equity curve so `∏(1 + r) == 1 +
        # total_return` and `max_drawdown` can see day-1 drawdowns.
        prev_total = self._equity_curve[-1].total_equity if self._equity_curve else None
        if prev_total is None:
            # First trading day: benchmark the return against initial_cash
            # (task 17) so a first-day P&L flows into the return series.
            daily_return = (
                total_equity / self._config.initial_cash - Decimal("1")
                if self._config.initial_cash > 0
                else Decimal("0")
            )
        elif prev_total == 0:
            # Defensive: a zero prior equity cannot produce a return ratio.
            daily_return = Decimal("0")
        else:
            daily_return = total_equity / prev_total - Decimal("1")
        # Drawdown: the running peak must include `initial_cash`, otherwise
        # a first-day loss is silently lost once a later day's equity stays
        # below initial_cash but above the prior day's equity (task 17:
        # "回撤峰值序列以 initial_cash 为初始峰值").
        peak = self._config.initial_cash
        if self._equity_curve:
            peak = max(peak, *(pt.total_equity for pt in self._equity_curve))
        drawdown = (
            max(Decimal("0"), (peak - total_equity) / peak)
            if peak > 0
            else Decimal("0")
        )
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
        # Per-day position snapshot with the valuation price actually used
        # (today's close or a lookback close for suspended symbols).
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
        `SnapshotFileMissingError` (whole-day file gone) is an
        infrastructure failure and propagates as well.
        """
        try:
            bars = portal.get_bars(symbol, today, today)
        except SnapshotFileMissingError:
            raise
        except MissingDataError:
            return None
        if not bars:
            return None
        close = bars[0].close
        if close is None or close <= 0:
            return None
        return close

    @staticmethod
    def _lookback_price_or_none(
        portal: MarketDataPortal, symbol: str, today: str
    ) -> Optional[Decimal]:
        """Most recent valid close for `symbol` within the lookback window.

        Mirrors `DataView.current_price` exactly so the engine's valuation
        fallback agrees with what strategies see through `DataView`. Used
        only when `_close_price_or_none` returns None for the same day.
        """
        try:
            trading_days = portal.get_calendar(_lookback_start_date(today), today)
        except MissingDataError:
            trading_days = []
        if not trading_days:
            return None
        lookback = trading_days[-CURRENT_PRICE_LOOKBACK:]
        for day in reversed(lookback):
            try:
                bars = portal.get_bars(symbol, day, day)
            except SnapshotFileMissingError:
                raise
            except MissingDataError:
                continue
            if not bars:
                continue
            close = bars[0].close
            if close is not None and close > 0:
                return close
        return None

    def _sellable_for(self, symbol: str) -> int:
        pos = self._portfolio.positions.get(symbol)
        return pos.sellable_quantity if pos else 0

    def _on_open_match(
        self, today: str, pending: List[Order], context: Context
    ) -> None:
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
        # Task 18: fold out-of-universe rejections into the orders
        # table so the audit trail sees them. These orders never
        # reached the broker.
        for order in context._consume_out_of_universe_orders():
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

    # ------------------------------------------------------------------ #
    # Task 19: holdings-period factor diagnostics
    # ------------------------------------------------------------------ #

    def _run_factor_diagnostics(self, today: str, portal: MarketDataPortal) -> None:
        """For each currently-held symbol, run factor diagnostics.

        Reads the cumulative factor history for `today`, calls
        `analyze_factor_series` with the tighter holdings-period
        jump band (0.1% relative), and records observations on
        the `FactorDiagnosticCollector` and a `DATA_WARNING`
        event for each new anomaly.

        Only symbols with a non-zero position at day-end are scanned
        (task 19: "持仓涉及的标的"). A symbol that was fully sold
        (position back to zero) must NOT keep emitting holdings-period
        warnings — its holding period has ended, and the accumulated
        factor history is therefore reset.

        Diagnostics are pure observations: they MUST NOT mutate
        cash, positions, or equity (contract task 9 invariant).
        Missing factors or whole-day snapshot failures are silently
        skipped so the run continues (the existing
        `_snapshot_equity` / broker paths raise DATA_ERROR on
        infrastructure failures, and we do not want to raise a
        second error here).
        """
        relevant = {
            sym for sym, pos in self._portfolio.positions.items() if pos.quantity > 0
        }
        # Drop factor history for symbols no longer held so a future
        # re-entry does not compare against a pre-gap, stale factor.
        for sym in list(self._factor_history):
            if sym not in relevant:
                del self._factor_history[sym]
        for sym in sorted(relevant):
            history = self._factor_history.get(sym, [])
            new_factor_rows = self._load_factor_rows(sym, today)
            for d, f in new_factor_rows:
                if not history or history[-1][0] < d:
                    history.append((d, f))
            self._factor_history[sym] = history
            if len(history) < 2:
                continue
            diagnostics = analyze_factor_series(
                symbol=sym,
                expected_dates=[d for d, _ in history],
                factors=history,
                jump_band=self._holding_jump_band,
            )
            existing = {
                (d.symbol, d.date, d.kind, d.detail)
                for d in self._factor_diagnostics.all()
            }
            for diag in diagnostics:
                key = (diag.symbol, diag.date, diag.kind, diag.detail)
                if key in existing:
                    continue
                self._factor_diagnostics.record(diag)
                # Include the actual factor values in the audit event so
                # the human can verify the ex-date dividend event from
                # the event log alone (without re-reading the snapshot).
                prev_factor = self._prev_factor_before(history, diag.date)
                new_factor = self._factor_on(history, diag.date)
                detail = (
                    f"factor diagnostic: {diag.symbol} {diag.kind} on "
                    f"{diag.date}: factor {prev_factor} -> {new_factor}; "
                    f"{diag.detail}"
                )
                self._event_log.record(
                    EngineEvent(
                        date=today,
                        phase=EventType.DATA_WARNING,
                        detail=detail,
                    )
                )

    def _load_factor_rows(self, symbol: str, today: str) -> List[Tuple[str, Decimal]]:
        """Read today's factor for `symbol`, returning a one-row list.

        Tolerates data-layer absences (MissingDataError /
        SnapshotFileMissingError / InvalidDataError) by returning an
        empty list (the analyzer will simply not see a row for today).
        This is intentional: factor-data absences are diagnostic
        observations, not run-aborting failures. Programming errors
        (anything that is NOT a `DataError`) still propagate.
        """
        try:
            rows = self.portal.get_factor(symbol, today, today)
        except DataError:
            return []
        return [(today, f) for _, f in rows]

    @staticmethod
    def _prev_factor_before(
        history: List[Tuple[str, Decimal]], date: str
    ) -> Optional[Decimal]:
        """Return the most recent factor in `history` strictly
        before `date`, or `None` if no earlier row exists.
        """
        prev: Optional[Decimal] = None
        for d, f in history:
            if d < date:
                prev = f
            else:
                break
        return prev

    @staticmethod
    def _factor_on(history: List[Tuple[str, Decimal]], date: str) -> Optional[Decimal]:
        for d, f in history:
            if d == date:
                return f
        return None
