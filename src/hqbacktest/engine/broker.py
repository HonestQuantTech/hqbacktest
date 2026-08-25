"""SimulatedBroker: market-on-open matching with rule set and cost model.

Rules:
    * Only `OrderType.MARKET` orders are supported; any other type is
      rejected by `Context` before reaching the broker.
    * The bar for `today` is read first: `MissingDataError` (suspended /
      no bar) becomes `bar_available=False` for the rule set, while
      `InvalidDataError` / I/O errors propagate and abort the run.
    * Each order then goes through `TradingRuleSet.first_denial`; the
      first denial short-circuits with a typed `RejectReason`.
    * Fees come from `CostModel.compute(order, price, quantity)`; the
      default model charges commission (with floor) on both sides and
      stamp tax on SELL.
    * Ledger-side rejections (`InsufficientCashError`,
      `InsufficientSharesError`) are produced by `Portfolio.apply_fill`
      and converted by the engine into typed rejections.

Batch-matching order (A-share convention):
    * Within a single `OPEN_MATCH(today)` batch, SELL orders match
      **before** BUY orders. This mirrors "卖出资金当日可用": the
      proceeds of a SELL can fund a same-batch BUY, so a rotation
      ("卖旧买新") is not falsely rejected for INSUFFICIENT_CASH.
    * The rule set's `InsufficientCashRule` checks against a rolling
      `running_cash` that starts at `portfolio_cash`, increases by each
      successful SELL's net proceeds, and decreases by each successful
      BUY's cost. The ordering within each side (SELL-only or BUY-only)
      preserves the strategy's submission order.
"""

from decimal import Decimal
from typing import Callable, List, Optional, Tuple

from ..data.errors import (
    MissingDataError,
    SnapshotFileMissingError,
)
from ..data.portal import MarketDataPortal
from ..domain.bar import Bar
from ..domain.enums import EventType, OrderStatus, RejectReason, Side
from ..domain.fill import Fill
from ..domain.money import PRICE_QUANT, quantize_cash
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
        the engine's view of the portfolio so the rule set stays pure;
        the value is captured **before** the batch runs (T+1 state from
        the previous day's settlement), and is not re-checked after each
        SELL because a SELL only reduces `sellable_quantity`, so stale
        values only over-estimate availability (fail-safe for the rule).

        Orders are partitioned into SELLs and BUYs. All SELLs match
        first (in submission order) so that their proceeds are
        available to fund the subsequent BUYs (rolling cash). The
        partition is a stable sort: the relative order within each side
        is preserved, but cross-side order is normalized to
        [SELLs..., BUYs...] as required by A-share convention.

        Results are returned in **matching order** ([SELLs..., BUYs...]),
        NOT submission order: the engine applies fills in the returned
        order, so a BUY must be applied only after its funding SELL has
        already credited the portfolio's cash. Re-assembling into
        submission order would break the rolling-cash guarantee (a BUY
        submitted before its funding SELL would be applied first and
        falsely rejected for INSUFFICIENT_CASH).
        """
        sell_orders = [o for o in orders if o.side is Side.SELL]
        buy_orders = [o for o in orders if o.side is Side.BUY]
        # Stable partition: preserve relative submission order within each
        # side (the original `orders` list is already insertion-ordered).
        ordered = sell_orders + buy_orders
        running_cash = quantize_cash(portfolio_cash)
        results: List[MatchResult] = []
        for order in ordered:
            match_result = self._match_one(
                order,
                portal,
                today,
                rule_set,
                running_cash,
                sellable_quantity_for(order.symbol),
            )
            results.append(match_result)
            # Roll cash: only successful fills update the running balance.
            # `net_amount()` is already signed (SELL proceeds positive,
            # BUY costs negative), so a single addition covers both sides.
            _, fill, _, _ = match_result
            if fill is not None:
                running_cash = quantize_cash(running_cash + fill.net_amount())
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

        # Read the bar for today.
        #   * `MissingDataError` (per-symbol gap: suspended / delisted /
        #     pre-IPO): the bar is simply unavailable. The order is rejected
        #     by the rule set (`bar_available=False`); the run continues.
        #   * `SnapshotFileMissingError` (the whole daily file is missing
        #     on disk): a data infrastructure failure. It MUST propagate so
        #     the engine aborts the run with `RunFailed` rather than
        #     silently treating the snapshot as empty.
        #   * `InvalidDataError` / I/O errors: infrastructure failures and
        #     propagate.
        try:
            bars = portal.get_bars(order.symbol, today, today)
        except SnapshotFileMissingError:
            raise
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
