"""Context: read-only façade for strategies with order-intent API.

Public surface required by `BaseStrategy`:
    * read-only accessors: `cash`, `positions`, `position`, `universe`,
      `pending_orders`, `history`, `current_price`, `now`, `phase`,
      `visible_through`;
    * order intents: `order`, `order_value`, `order_target`,
      `order_target_value`, `order_target_percent`, `cancel_order`;
    * lifecycle hooks driven by `BacktestEngine` (initialized / run_finished).

The Context never mutates the ledger directly. `Portfolio.apply_fill` is
the single writer; here we only append `Order` objects to
`pending_orders`. Strategy code therefore cannot reach the broker
directly — every order path funnels through the engine.

Isolation rules enforced here (contract §4):
    * Date / phase / data view are engine-owned: they can only be changed
      through `_set_*` hooks, never by strategy code.
    * Market data is only readable while the scheduler has published a
      `DataView` for the current phase; `initialize` / `SESSION_START` have
      no data access at all.
    * Orders may only be submitted from `BEFORE_TRADING_START` or
      `BAR_CLOSE` (contract §4 "可下单" column).
    * `set_universe` may only be called from `initialize`; the universe is
      locked once `initialize` returns.
    * All order paths funnel through `_create_order`, so the order-type
      allow-list and symbol validation cannot be skipped by the
      convenience helpers.
"""

from dataclasses import replace
from decimal import Decimal
from typing import Dict, List, Optional

from ..data.data_view import DataView
from ..data.validators import validate_symbol
from ..domain.enums import EventType, OrderType, OrderStatus, RejectReason, Side
from ..domain.money import LOT_SIZE, is_positive, quantize_cash, round_lot
from ..domain.order import Order
from ..domain.portfolio import Portfolio
from ..domain.position import Position
from .errors import (
    CallbackAfterRunError,
    DoubleInitializationError,
    NoPriceForOrderError,
    NotInitializedError,
    StrategyLifecycleError,
    UnsupportedOrderTypeError,
)
from .events import EngineEvent, EventLog
from .intents import (
    normalize_percent,
    quantity_from_value,
    side_from_quantity,
    signed_diff_to_lots,
    target_value_for_percent,
)

# Phases from which a strategy may submit or cancel orders (contract §4).
ORDERABLE_PHASES = frozenset({EventType.BEFORE_TRADING_START, EventType.BAR_CLOSE})


class Context:
    """Read-only strategy façade plus order-intent helpers.

    The strategy can only mutate the universe (via `set_universe`, during
    `initialize`) and `_pending_orders` (via order intents). The ledger,
    the current date, the phase and the data view are engine-owned.
    """

    def __init__(
        self,
        current_date: str,
        portfolio: Portfolio,
        event_log: EventLog,
        data_view: Optional[DataView] = None,
    ) -> None:
        self._current_date: str = current_date
        self._phase: Optional[EventType] = None
        self._portfolio = portfolio
        self._event_log = event_log
        self._data_view = data_view
        self._universe: List[str] = []
        self._pending_orders: List[Order] = []
        self._out_of_universe_orders: List[Order] = []
        self._initialized: bool = False
        self._universe_locked: bool = False
        self._run_finished: bool = False
        self._order_counter: int = 0

    # ------------------------------------------------------------------ #
    # Engine hooks (do not call from strategy code)
    # ------------------------------------------------------------------ #

    def _mark_initialized(self) -> None:
        if self._initialized:
            raise DoubleInitializationError(
                "strategy.initialize() called more than once in the same run"
            )
        self._initialized = True

    def _lock_universe(self) -> None:
        self._universe_locked = True

    def _mark_run_finished(self) -> None:
        self._run_finished = True

    def _set_date(self, date: str) -> None:
        self._current_date = date

    def _set_phase(self, phase: Optional[EventType]) -> None:
        self._phase = phase

    def _set_data_view(self, view: Optional[DataView]) -> None:
        self._data_view = view

    def _consume_pending_orders(self) -> List[Order]:
        orders = list(self._pending_orders)
        self._pending_orders = []
        return orders

    def _consume_out_of_universe_orders(self) -> List[Order]:
        """Return and clear out-of-universe orders for audit-trail merge."""
        orders = list(self._out_of_universe_orders)
        self._out_of_universe_orders = []
        return orders

    def _has_out_of_universe_orders(self) -> bool:
        """True when out-of-universe rejections are waiting to be drained.

        Peek-only (does not clear): the scheduler uses this to decide
        whether to invoke the matcher, while the engine drains the list
        via `_consume_out_of_universe_orders`.
        """
        return bool(self._out_of_universe_orders)

    # ------------------------------------------------------------------ #
    # Guards
    # ------------------------------------------------------------------ #

    def _require_active(self, what: str) -> None:
        if not self._initialized:
            raise NotInitializedError(f"{what} called before strategy.initialize()")
        if self._run_finished:
            raise CallbackAfterRunError(
                f"{what} called after BacktestEngine.run() returned"
            )

    def _require_data(self, what: str) -> DataView:
        """Market data is only readable in phases with a published DataView."""
        self._require_active(what)
        if self._data_view is None:
            phase = self._phase.name if self._phase is not None else "UNKNOWN"
            raise StrategyLifecycleError(
                f"{what}: no market data is visible in phase {phase}"
            )
        return self._data_view

    def _require_orderable(self, what: str) -> None:
        """Orders only from BEFORE_TRADING_START / BAR_CLOSE (contract §4)."""
        self._require_active(what)
        if self._phase not in ORDERABLE_PHASES:
            phase = self._phase.name if self._phase is not None else "UNKNOWN"
            raise StrategyLifecycleError(
                f"{what} is not allowed in phase {phase}; orders may only be "
                f"submitted from before_trading_start or on_bar"
            )

    # ------------------------------------------------------------------ #
    # Read-only accessors (contract: only reads)
    # ------------------------------------------------------------------ #

    @property
    def now(self) -> str:
        self._require_active("now")
        return self._current_date

    @property
    def current_date(self) -> str:
        self._require_active("current_date")
        return self._current_date

    @property
    def phase(self) -> Optional[EventType]:
        self._require_active("phase")
        return self._phase

    @property
    def visible_through(self) -> str:
        """Hard cap on visible data for the current phase ("" if no view)."""
        self._require_active("visible_through")
        return self._data_view.visible_through if self._data_view is not None else ""

    def cash(self) -> Decimal:
        self._require_active("cash")
        return self._portfolio.cash

    def total_equity(self) -> Decimal:
        """`cash + market_value` against the current visible prices."""
        self._require_active("total_equity")
        prices = self._collect_prices()
        return quantize_cash(
            self._portfolio.cash + self._portfolio.market_value(prices)
        )

    def positions(self) -> Dict[str, Position]:
        """Per-symbol holdings. Copies: mutating them never touches the ledger."""
        self._require_active("positions")
        return {sym: replace(pos) for sym, pos in self._portfolio.positions.items()}

    def position(self, symbol: str) -> Optional[Position]:
        """One symbol's holding. A copy: mutating it never touches the ledger."""
        self._require_active("position")
        pos = self._portfolio.positions.get(symbol)
        return replace(pos) if pos is not None else None

    def universe(self) -> List[str]:
        """Snapshot of the strategy's declared universe (defensive copy)."""
        self._require_active("universe")
        return list(self._universe)

    def historical_universe(self) -> List[str]:
        """The historical stock list as of the current `visible_through`.

        This is the only universe accessor that reads through the data
        portal. It is constrained by `visible_through` and must not be
        used to read future data. When no `DataView` is published
        (e.g. in `initialize`), an empty list is returned.
        """
        self._require_active("historical_universe")
        if self._data_view is None:
            return []
        return self._data_view.universe()

    def pending_orders(self) -> List[Order]:
        """Snapshot of in-flight orders (engine clears them at matching).

        The returned list and its `Order` elements are defensive copies /
        frozen instances — strategies cannot mutate the engine's view
        of the order.
        """
        self._require_active("pending_orders")
        return list(self._pending_orders)

    def history(
        self, symbol: str, field: str = "close", bar_count: int = 1
    ) -> List[Decimal]:
        """Windowed view onto the historical bars for `symbol`."""
        view = self._require_data("history")
        return view.history(symbol, field=field, bar_count=bar_count)

    def current_price(self, symbol: str) -> Optional[Decimal]:
        view = self._require_data("current_price")
        return view.current_price(symbol)

    def record_event(self, event: EngineEvent) -> None:
        self._require_active("record_event")
        self._event_log.record(event)

    # ------------------------------------------------------------------ #
    # Universe declaration (strategy -> engine; initialize only)
    # ------------------------------------------------------------------ #

    def set_universe(self, symbols) -> None:
        """Declare the strategy's tradeable universe (contract: initialize only)."""
        self._require_active("set_universe")
        if self._universe_locked:
            raise StrategyLifecycleError(
                "set_universe may only be called from initialize(); the "
                "universe is immutable once the run starts"
            )
        if isinstance(symbols, str):
            symbols = [symbols]
        symbols = list(symbols)
        for sym in symbols:
            validate_symbol(sym)
        # Preserve order, dedupe.
        seen: Dict[str, None] = {}
        for sym in symbols:
            seen.setdefault(sym, None)
        self._universe = list(seen.keys())

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _collect_prices(self) -> Dict[str, Decimal]:
        if self._data_view is None:
            return {}
        prices: Dict[str, Decimal] = {}
        for symbol in self._portfolio.positions:
            price = self._data_view.current_price(symbol)
            if price is not None:
                prices[symbol] = price
        return prices

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"O{self._current_date}-{self._order_counter:06d}"

    def _coerce_amount(self, value, name: str) -> Decimal:
        """Coerce a monetary value to `Decimal`, accepting int/str/Decimal.

        `float` and `bool` are rejected (contract rule 5: no binary
        float enters the ledger), and NaN / Inf are rejected. Lets
        strategies use literal cash values (`15000`, `'15000'`)
        without wrapping them in `Decimal(...)`.
        """
        if isinstance(value, bool):
            raise StrategyLifecycleError(f"{name} must be a number, got bool")
        if isinstance(value, float):
            raise StrategyLifecycleError(
                f"{name} must not be float (contract rule 5); got {value!r}"
            )
        if not isinstance(value, (Decimal, int, str)):
            raise StrategyLifecycleError(
                f"{name} must be Decimal/int/str, got {type(value).__name__}"
            )
        if isinstance(value, (int, str)):
            try:
                value = Decimal(str(value))
            except Exception as exc:
                raise StrategyLifecycleError(
                    f"{name}={value!r} is not a valid Decimal: {exc}"
                ) from exc
        if not value.is_finite():
            raise StrategyLifecycleError(f"{name} must be finite, got {value}")
        return value

    # ------------------------------------------------------------------ #
    # Order intents (contract: never mutates the ledger)
    # ------------------------------------------------------------------ #

    def order(
        self,
        symbol: str,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
    ) -> Optional[Order]:
        """Place a direct order for `quantity` shares of `symbol`."""
        self._require_orderable("order")
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise StrategyLifecycleError(
                f"quantity must be int, got {type(quantity).__name__}"
            )
        if quantity == 0:
            return None
        return self._create_order(
            symbol=symbol,
            side=side_from_quantity(quantity),
            quantity=abs(quantity),
            order_type=order_type,
        )

    def order_value(
        self,
        symbol: str,
        value,
        order_type: OrderType = OrderType.MARKET,
    ) -> Optional[Order]:
        """Place an order for `value` worth of `symbol` (positive => BUY).

        `value` may be a `Decimal`, `int`, or numeric string; `float`
        remains forbidden (contract rule 5). Widening the accepted
        types here lets strategies use literal cash values without
        first wrapping them in `Decimal(...)`.
        """
        self._require_orderable("order_value")
        value = self._coerce_amount(value, "value")
        if value == 0:
            return None
        price = self.current_price(symbol)
        if price is None:
            raise NoPriceForOrderError(
                f"no visible price for {symbol} when sizing order_value"
            )
        quantity = quantity_from_value(value, price, LOT_SIZE)
        if quantity == 0:
            return None
        return self._create_order(
            symbol=symbol,
            side=side_from_quantity(quantity),
            quantity=abs(quantity),
            order_type=order_type,
        )

    def order_target(
        self,
        symbol: str,
        target_quantity: int,
        order_type: OrderType = OrderType.MARKET,
    ) -> Optional[Order]:
        """Reconcile holdings towards `target_quantity` shares."""
        self._require_orderable("order_target")
        if not isinstance(target_quantity, int) or isinstance(target_quantity, bool):
            raise StrategyLifecycleError("target_quantity must be int")
        if target_quantity < 0:
            raise StrategyLifecycleError(
                f"target_quantity must be non-negative, got {target_quantity}"
            )
        current = self._portfolio.positions.get(symbol)
        current_qty = current.quantity if current else 0
        diff = signed_diff_to_lots(target_quantity, current_qty, LOT_SIZE)
        if diff == 0:
            return None
        return self._create_order(
            symbol=symbol,
            side=side_from_quantity(diff),
            quantity=abs(diff),
            order_type=order_type,
        )

    def order_target_value(
        self,
        symbol: str,
        target_value,
        order_type: OrderType = OrderType.MARKET,
    ) -> Optional[Order]:
        """Reconcile holdings towards `target_value` worth of `symbol`.

        `target_value` may be a `Decimal`, `int`, or numeric string;
        `float` remains forbidden (contract rule 5).
        """
        self._require_orderable("order_target_value")
        target_value = self._coerce_amount(target_value, "target_value")
        if target_value < 0:
            raise StrategyLifecycleError(
                f"target_value must be non-negative, got {target_value}"
            )
        if target_value == 0:
            return self.order_target(symbol, 0, order_type=order_type)
        price = self.current_price(symbol)
        if price is None:
            raise NoPriceForOrderError(
                f"no visible price for {symbol} when sizing order_target_value"
            )
        current = self._portfolio.positions.get(symbol)
        current_qty = current.quantity if current else 0
        current_value = quantize_cash(price * Decimal(current_qty))
        diff_value = target_value - current_value
        if diff_value == 0:
            return None
        quantity = quantity_from_value(diff_value, price, LOT_SIZE)
        if quantity == 0:
            return None
        return self._create_order(
            symbol=symbol,
            side=side_from_quantity(quantity),
            quantity=abs(quantity),
            order_type=order_type,
        )

    def order_target_percent(
        self,
        symbol: str,
        percent,
        order_type: OrderType = OrderType.MARKET,
    ) -> Optional[Order]:
        """Reconcile holdings towards `percent` of current total equity."""
        self._require_orderable("order_target_percent")
        fraction = normalize_percent(percent)
        if fraction == 0:
            return self.order_target(symbol, 0, order_type=order_type)
        equity = self.total_equity()
        target_value = target_value_for_percent(fraction, equity)
        return self.order_target_value(symbol, target_value, order_type=order_type)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order by ID. Returns True if removed, False if absent."""
        self._require_orderable("cancel_order")
        for i, order in enumerate(self._pending_orders):
            if order.order_id == order_id:
                order.transition(
                    OrderStatus.CANCELLED,
                    at=self._current_date,
                )
                self._pending_orders.pop(i)
                self._event_log.record(
                    EngineEvent(
                        date=self._current_date,
                        phase=EventType.ORDER_CANCELLED,
                        order_id=order_id,
                        detail="cancelled by strategy",
                    )
                )
                return True
        return False

    # ------------------------------------------------------------------ #
    # Final builders
    # ------------------------------------------------------------------ #

    def _create_order(
        self,
        *,
        symbol: str,
        side: Side,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
    ) -> Optional[Order]:
        # Contract rule 7: every order path funnels through here, so the
        # order-type allow-list and symbol validation cannot be skipped by
        # the convenience helpers.
        if not isinstance(order_type, OrderType):
            raise UnsupportedOrderTypeError(
                f"only OrderType enum accepted, got {type(order_type).__name__}"
            )
        if order_type is not OrderType.MARKET:
            raise UnsupportedOrderTypeError(
                f"v0.1 only supports MARKET orders; got {order_type.name}"
            )
        validate_symbol(symbol)
        if not is_positive(Decimal(quantity)):
            raise StrategyLifecycleError(f"quantity must be positive, got {quantity}")
        # Lot-alignment applies to BUY only. A-share rules allow odd-lot
        # SELLs so positions holding non-lot quantities can be fully
        # closed; round_lot() on a SELL would silently shrink the order
        # (e.g. 150 -> 100), violating the contract that the broker sees
        # the exact share count the strategy submitted.
        if side is Side.BUY:
            lot_aligned = round_lot(quantity, lot_size=LOT_SIZE)
            if lot_aligned == 0:
                raise StrategyLifecycleError(
                    f"quantity {quantity} is below one lot of {LOT_SIZE} shares"
                )
            final_quantity = lot_aligned
        else:
            final_quantity = quantity
        # When a universe has been declared, orders for symbols outside
        # it must be rejected (typed reason + audit-trail event). When
        # the strategy has not called `set_universe`, the universe is
        # empty and trading is unrestricted.
        if self._universe and symbol not in self._universe:
            return self._reject_out_of_universe(
                symbol=symbol, side=side, quantity=final_quantity
            )
        # Guaranteed by _require_orderable in every public order method.
        created_session = self._phase
        order = Order(
            order_id=self._next_order_id(),
            symbol=symbol,
            side=side,
            quantity=final_quantity,
            order_type=order_type,
            created_at=self._current_date,
            created_session=created_session,
        )
        # Move to ACCEPTED immediately so the broker only has to handle
        # the matching step. Engine-side validity checks will mark
        # REJECTED if necessary.
        order.transition(OrderStatus.ACCEPTED, at=self._current_date)
        order.transition(OrderStatus.PENDING, at=self._current_date)
        self._pending_orders.append(order)
        self._event_log.record(
            EngineEvent(
                date=self._current_date,
                phase=EventType.ORDER_CREATED,
                order_id=order.order_id,
                detail=(
                    f"{side.name} {final_quantity} {symbol} "
                    f"(session={created_session.name})"
                ),
            )
        )
        return order

    def _reject_out_of_universe(
        self,
        *,
        symbol: str,
        side: Side,
        quantity: int,
    ) -> Optional[Order]:
        """Build a REJECTED order for a symbol outside the declared
        universe. Records ORDER_REJECTED + ORDER_CREATED events so the
        audit trail is complete. The order is NOT appended to
        `_pending_orders` so the broker never sees it (REJECTED is a
        terminal status and would corrupt the broker's state machine);
        instead it lands in `_out_of_universe_orders` and is folded
        into the engine's `orders_table` at result build.
        """
        created_session = self._phase
        order = Order(
            order_id=self._next_order_id(),
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            created_at=self._current_date,
            created_session=created_session,
        )
        order.transition(OrderStatus.ACCEPTED, at=self._current_date)
        # Stamp the reason onto the Order itself (not only the event log)
        # so `orders_table.reject_reason` and the ORDER_REJECTED event
        # agree.
        order.transition(
            OrderStatus.REJECTED,
            at=self._current_date,
            reason=RejectReason.OUT_OF_UNIVERSE,
            detail=(
                f"{symbol} not in declared universe "
                f"({len(self._universe)} symbols); order rejected"
            ),
        )
        self._out_of_universe_orders.append(order)
        self._event_log.record(
            EngineEvent(
                date=self._current_date,
                phase=EventType.ORDER_CREATED,
                order_id=order.order_id,
                detail=f"{side.name} {quantity} {symbol} (session={created_session.name})",
            )
        )
        self._event_log.record(
            EngineEvent(
                date=self._current_date,
                phase=EventType.ORDER_REJECTED,
                order_id=order.order_id,
                error=RejectReason.OUT_OF_UNIVERSE.name,
                detail=(
                    f"{symbol} not in declared universe "
                    f"({len(self._universe)} symbols); order rejected"
                ),
            )
        )
        return order
