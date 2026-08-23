"""SimulatedBroker: market-on-open matching for v0.1.

Rules (task 7 contract):
    * Only `OrderType.MARKET` orders are supported; any other type is
      rejected by `Context` before reaching the broker.
    * For each pending order the broker reads the bar for `today` and
      attempts to fill at `bar.open`.
    * Deterministic data-side rejection reasons (broker layer):
        - missing bar / missing open price → `MISSING_DATA`
        - non-positive open price            → `INVALID_PRICE`
    * Ledger-side rejections are detected by `Portfolio.apply_fill`:
        - insufficient cash (BUY)            → `INSUFFICIENT_CASH`
        - insufficient sellable shares (SELL) → `INSUFFICIENT_SHARES`
      The broker produces the fill unconditionally; the engine applies it
      and, on `ValueError`, converts the failure into a `REJECTED` outcome
      on the order. This keeps the broker pure (no ledger dependency).
    * Cost model and fees are NOT applied (task 8); all fees are zero in v0.1.
    * Settlement (T+1 sellable, `settle_t1`) is driven by the engine.

The broker is pure: it does not mutate the ledger. It returns a list of
`(order, fill|None, reject_reason|None, reject_detail|None)` tuples; the
engine is responsible for either calling `Portfolio.apply_fill(fill)` or
recording the rejection on the order.
"""

from typing import List, Optional, Tuple

from ..data.errors import MissingDataError
from ..data.portal import MarketDataPortal
from ..domain.bar import Bar
from ..domain.enums import EventType, OrderStatus, RejectReason
from ..domain.fill import Fill
from ..domain.money import PRICE_QUANT
from ..domain.order import Order

MatchResult = Tuple[Order, Optional[Fill], Optional[RejectReason], Optional[str]]


class SimulatedBroker:
    """Match pending market orders at `OPEN_MATCH` against today's open price."""

    def __init__(self) -> None:
        self._next_fill_seq = 0

    def match(
        self,
        orders: List[Order],
        portal: MarketDataPortal,
        today: str,
    ) -> List[MatchResult]:
        """Match each order in `orders` and return the result list."""
        results: List[MatchResult] = []
        for order in orders:
            results.append(self._match_one(order, portal, today))
        return results

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _match_one(
        self,
        order: Order,
        portal: MarketDataPortal,
        today: str,
    ) -> MatchResult:
        if order.status is not OrderStatus.PENDING:
            return (
                order,
                None,
                RejectReason.OTHER,
                f"order not PENDING (status={order.status.name})",
            )

        # Read the bar for today; missing data => rejection. Only
        # `MissingDataError` is a business rejection: corrupt snapshots
        # (`InvalidDataError`) or I/O failures are infrastructure errors and
        # must abort the run (contract rule 12), not silently reject orders.
        try:
            bars = portal.get_bars(order.symbol, today, today)
        except MissingDataError as exc:
            return order, None, RejectReason.MISSING_DATA, str(exc)
        if not bars:
            return order, None, RejectReason.MISSING_DATA, "no bar for today"

        bar: Bar = bars[0]
        if bar.open is None or bar.open <= 0:
            return order, None, RejectReason.INVALID_PRICE, f"open={bar.open}"

        price = bar.open.quantize(PRICE_QUANT)
        return (
            order,
            self._build_fill(order=order, today=today, price=price),
            None,
            None,
        )

    def _build_fill(
        self,
        *,
        order: Order,
        today: str,
        price,
    ) -> Fill:
        self._next_fill_seq += 1
        # Use a globally monotonic fill_id keyed on the run date and the
        # broker's counter. The broker is reset per run by the engine
        # (a fresh broker instance per `run()`).
        fill_id = f"F{today}-{self._next_fill_seq:06d}"
        return Fill.from_trade(
            fill_id=fill_id,
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=price,
            commission=0,
            stamp_tax=0,
            other_fee=0,
            filled_at=today,
            session=EventType.OPEN_MATCH,
        )
