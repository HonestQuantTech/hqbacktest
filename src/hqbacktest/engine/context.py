"""Context: read-only façade for strategies with order-intent API.

Task 6 adds the full surface required by `BaseStrategy`:
    * read-only accessors: `cash`, `positions`, `position`, `universe`,
      `pending_orders`, `history`, `current_price`, `now`, `phase`,
      `visible_through`;
    * order intents: `order`, `order_value`, `order_target`,
      `order_target_value`, `order_target_percent`, `cancel_order`;
    * lifecycle hooks driven by `BacktestEngine` (initialized / run_finished).

The Context never mutates the ledger directly. `Portfolio.apply_fill` is
the single writer (task 7); here we only append `Order` objects to
`pending_orders`. Strategy code therefore cannot bypass the broker.

Isolation rules enforced here (contract §4 and task 6 goals):
    * Date / phase / data view are engine-owned: they can only be changed
      through `_set_*` hooks, never by strategy code.
    * Market data is only readable while the scheduler has published a
      `DataView` for the current phase; `initialize` / `SESSION_START` have
      no data access at all.
    * Orders may only be submitted from `BEFORE_TRADING_START` or
      `BAR_CLOSE` (contract §4 "可下单" column).
    * `set_universe` may only be called from `initialize`; the universe is
      locked once `initialize` returns (contract §4 配套约束).
"""

from dataclasses import replace
from decimal import Decimal
from typing import Dict, List, Optional

from ..data.data_view import DataView
from ..data.validators import validate_symbol
from ..domain.enums import EventType, OrderType, OrderStatus, Side
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
        self._require_active("universe")
        return list(self._universe)

    def pending_orders(self) -> List[Order]:
        """Snapshot of in-flight orders (engine clears them at matching)."""
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
        value: Decimal,
        order_type: OrderType = OrderType.MARKET,
    ) -> Optional[Order]:
        """Place an order for `value` worth of `symbol` (positive => BUY)."""
        self._require_orderable("order_value")
        if not isinstance(value, Decimal):
            raise StrategyLifecycleError(
                f"value must be Decimal, got {type(value).__name__}"
            )
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
        target_value: Decimal,
        order_type: OrderType = OrderType.MARKET,
    ) -> Optional[Order]:
        """Reconcile holdings towards `target_value` worth of `symbol`."""
        self._require_orderable("order_target_value")
        if not isinstance(target_value, Decimal):
            raise StrategyLifecycleError(
                f"target_value must be Decimal, got {type(target_value).__name__}"
            )
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
    ) -> Order:
        # Contract rule 7: every order path funnels through here, so the
        # order-type allow-list and symbol validation cannot be bypassed by
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
        lot_aligned = round_lot(quantity, lot_size=LOT_SIZE)
        if lot_aligned == 0:
            raise StrategyLifecycleError(
                f"quantity {quantity} is below one lot of {LOT_SIZE} shares"
            )
        # Guaranteed by _require_orderable in every public order method.
        created_session = self._phase
        order = Order(
            order_id=self._next_order_id(),
            symbol=symbol,
            side=side,
            quantity=lot_aligned,
            order_type=order_type,
            created_at=self._current_date,
            created_session=created_session,
        )
        # Move to ACCEPTED immediately so the broker (task 7) only has to
        # handle the matching step. Engine-side validity checks will mark
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
                    f"{side.name} {lot_aligned} {symbol} "
                    f"(session={created_session.name})"
                ),
            )
        )
        return order
