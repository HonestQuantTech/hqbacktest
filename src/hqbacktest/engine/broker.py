"""SimulatedBroker: market-on-open matching with rule set and cost model.

Rules (task 7 + task 8):
    * Only `OrderType.MARKET` orders are supported; any other type is
      rejected by `Context` before reaching the broker.
    * The bar for `today` is read first: `MissingDataError` (suspended /
      no bar) becomes `bar_available=False` for the rule set, while
      `InvalidDataError` and I/O errors propagate and abort the run.
    * Each order then goes through `TradingRuleSet.first_denial`; the
      first denial short-circuits with a typed `RejectReason`.
    * Fees come from `CostModel.compute(order, price, quantity)`; the
      default model charges commission (with floor) on both sides and
      stamp tax on SELL.
    * Ledger-side rejections (`InsufficientCashError`,
      `InsufficientSharesError`) are produced by `Portfolio.apply_fill`
      and converted by the engine into typed rejections.
"""

from decimal import Decimal
from typing import Callable, List, Optional, Tuple

from ..data.errors import MissingDataError
from ..data.portal import MarketDataPortal
from ..domain.bar import Bar
from ..domain.enums import EventType, OrderStatus, RejectReason, Side
from ..domain.fill import Fill
from ..domain.money import PRICE_QUANT
from ..domain.order import Order
from .cost_model import CostModel, DefaultCostModel
from .rule_set import RuleCheckContext, TradingRuleSet

MatchResult = Tuple[Order, Optional[Fill], Optional[RejectReason], Optional[str]]


class SimulatedBroker:
    """Match pending market orders at `OPEN_MATCH` against today's open price."""

    def __init__(self, cost_model: Optional[CostModel] = None) -> None:
        self._cost_model: CostModel = cost_model or DefaultCostModel()
        self._next_fill_seq = 0

    def cost_model(self) -> CostModel:
        return self._cost_model

    def match(
        self,
        orders: List[Order],
        portal: MarketDataPortal,
        today: str,
        rule_set: TradingRuleSet,
        portfolio_cash: Decimal,
        sellable_quantity_for: Callable[[str], int],
    ) -> List[MatchResult]:
        """Match each order in `orders` and return the result list.

        `sellable_quantity_for(symbol)` is a callable returning the current
        T+1 sellable shares for the symbol (0 if no position). It mirrors
        the engine's view of the portfolio so the rule set stays pure.

        `portfolio_cash` is a snapshot taken before this batch: for later
        orders in the same batch the rule-level cash check may be stale.
        That is safe because `Portfolio.apply_fill` re-checks every fill
        against the live ledger and is the final arbiter.
        """
        results: List[MatchResult] = []
        for order in orders:
            results.append(
                self._match_one(
                    order,
                    portal,
                    today,
                    rule_set,
                    portfolio_cash,
                    sellable_quantity_for(order.symbol),
                )
            )
        return results

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _match_one(
        self,
        order: Order,
        portal: MarketDataPortal,
        today: str,
        rule_set: TradingRuleSet,
        portfolio_cash: Decimal,
        sellable_quantity: int,
    ) -> MatchResult:
        if order.status is not OrderStatus.PENDING:
            return (
                order,
                None,
                RejectReason.OTHER,
                f"order not PENDING (status={order.status.name})",
            )

        # Read the bar for today. `MissingDataError` (suspended symbol /
        # no bar for this trading day) is a BUSINESS outcome: the order is
        # rejected via the rule set (contract §4: 停牌标的不可成交, but the
        # run continues). `InvalidDataError` / I/O errors are infrastructure
        # failures and propagate so the engine aborts with `RunFailed`.
        try:
            bars = portal.get_bars(order.symbol, today, today)
        except MissingDataError:
            bars = []
        bar_available = bool(bars)
        if not bar_available:
            proposed_price: Optional[Decimal] = None
        else:
            bar = bars[0]
            if bar.open is None or bar.open <= 0:
                proposed_price = None
            else:
                proposed_price = bar.open.quantize(PRICE_QUANT)

        # Pre-compute estimated fees so InsufficientCashRule can account
        # for them; falls back to zeros when no price is known.
        if proposed_price is not None:
            est = self._cost_model.compute(order, proposed_price, order.quantity)
            est_commission = est.commission
            est_stamp_tax = est.stamp_tax
        else:
            est_commission = Decimal("0")
            est_stamp_tax = Decimal("0")

        # Run the rule set with whatever information we have so far.
        rule_ctx = RuleCheckContext(
            today=today,
            proposed_price=proposed_price,
            proposed_quantity=order.quantity,
            sellable_quantity=sellable_quantity,
            portfolio_cash=portfolio_cash,
            bar_available=bar_available,
            estimated_commission=est_commission,
            estimated_stamp_tax=est_stamp_tax,
        )
        denial = rule_set.first_denial(order, rule_ctx)
        if denial is not None:
            return (
                order,
                None,
                denial.reason,
                (
                    f"rule:{denial.rule_name}: {denial.detail}"
                    if denial.detail
                    else f"rule:{denial.rule_name}"
                ),
            )

        # Rules passed; build the fill (cost computed via CostModel). A
        # custom rule set without InvalidPriceRule still cannot fill at an
        # unknown price — reject defensively instead of crashing.
        if proposed_price is None:
            return (
                order,
                None,
                RejectReason.INVALID_PRICE,
                f"no usable open price for {order.symbol} on {today}",
            )
        cost = self._cost_model.compute(order, proposed_price, order.quantity)
        return (
            order,
            self._build_fill(
                order=order,
                today=today,
                price=proposed_price,
                commission=cost.commission,
                stamp_tax=cost.stamp_tax,
                other_fee=cost.transfer_fee,
            ),
            None,
            None,
        )

    def _build_fill(
        self,
        *,
        order: Order,
        today: str,
        price,
        commission: Decimal,
        stamp_tax: Decimal,
        other_fee: Decimal,
    ) -> Fill:
        self._next_fill_seq += 1
        fill_id = f"F{today}-{self._next_fill_seq:06d}"
        return Fill.from_trade(
            fill_id=fill_id,
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=price,
            commission=commission,
            stamp_tax=stamp_tax,
            other_fee=other_fee,
            filled_at=today,
            session=EventType.OPEN_MATCH,
        )
