"""TradingRuleSet: pluggable A-share rules.

The rule set runs BEFORE the broker constructs a fill. Any rule may
return a denial with a deterministic `RejectReason`; the engine then
records the denial as an `ORDER_CANCELLED` (cancellation) or
`ORDER_REJECTED` event with the rule name.

v0.1 includes six default rules:
    * LongOnlyRule         - only BUY / SELL allowed (no short selling).
    * LotSizeRule          - BUY quantity must be a multiple of 100
                             (odd-lot SELLs are allowed, per A-share rules).
    * NonTradingDayRule    - no matching when the portal has no bar for today.
    * InvalidPriceRule     - open price must be strictly positive.
    * InsufficientCashRule - BUY cost (incl. fees) <= available cash.
    * T1SellableRule       - SELL quantity <= position.sellable_quantity.

Rules whose data is unreliable in v0.1 (ST, 涨跌停, 新股, 北交所) are NOT
implemented; their `Rule` slots can be added later without changing the
engine contract.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Protocol, Sequence, runtime_checkable

from ..domain.enums import RejectReason, Side
from ..domain.order import Order


# --------------------------------------------------------------------- #
# Check context (information passed to each rule)
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuleCheckContext:
    """Inputs available to every rule when checking one order.

    The same context is reused across rules for a given order; rules must
    not mutate it.
    """

    today: str  # YYYYMMDD
    proposed_price: Optional[Decimal]  # None when no bar / no data
    proposed_quantity: int  # the order's quantity
    sellable_quantity: int  # position.sellable_quantity (0 if no position)
    portfolio_cash: Decimal
    bar_available: bool  # True iff the broker could read a bar for today
    estimated_commission: Decimal = Decimal("0")  # v0.1: commission only
    estimated_stamp_tax: Decimal = Decimal("0")


# --------------------------------------------------------------------- #
# Rule + result
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuleResult:
    """Outcome of one rule check.

    `allowed=True` means the rule passes; the engine keeps evaluating the
    remaining rules. `allowed=False` carries a `reason` and a free-form
    `detail` (e.g. "available 100, requested 200") that the audit log
    captures.
    """

    allowed: bool
    rule_name: str
    reason: Optional[RejectReason] = None
    detail: str = ""


@runtime_checkable
class Rule(Protocol):
    """One named rule. Returns `None` to allow or a denial `RuleResult`."""

    name: str

    def check(self, order: Order, ctx: RuleCheckContext) -> Optional[RuleResult]: ...


def _allow(rule_name: str) -> Optional[RuleResult]:
    return None


def _deny(
    rule_name: str,
    reason: RejectReason,
    detail: str = "",
) -> RuleResult:
    return RuleResult(allowed=False, rule_name=rule_name, reason=reason, detail=detail)


# --------------------------------------------------------------------- #
# v0.1 default rules
# --------------------------------------------------------------------- #


class LongOnlyRule:
    """Only BUY or SELL allowed (no short selling)."""

    name = "long_only"

    def check(self, order: Order, ctx: RuleCheckContext) -> Optional[RuleResult]:
        if order.side in (Side.BUY, Side.SELL):
            return _allow(self.name)
        return _deny(
            self.name,
            RejectReason.OTHER,
            f"side {order.side.name} not allowed in v0.1",
        )


class LotSizeRule:
    """BUY quantity must be a multiple of `lot_size` (default 100).

    SELL orders are exempt: A-share rules allow odd-lot sells (零股卖出),
    so a position holding a non-lot multiple can still be closed.
    """

    name = "lot_size"

    def __init__(self, lot_size: int = 100) -> None:
        if lot_size <= 0:
            raise ValueError(f"lot_size must be positive, got {lot_size}")
        self._lot_size = lot_size

    def check(self, order: Order, ctx: RuleCheckContext) -> Optional[RuleResult]:
        if order.side is not Side.BUY:
            return _allow(self.name)
        if order.quantity % self._lot_size == 0:
            return _allow(self.name)
        return _deny(
            self.name,
            RejectReason.OTHER,
            f"buy quantity {order.quantity} is not a multiple of {self._lot_size}",
        )


class NonTradingDayRule:
    """No matching when the portal has no bar for `today`."""

    name = "non_trading_day"

    def check(self, order: Order, ctx: RuleCheckContext) -> Optional[RuleResult]:
        if ctx.bar_available:
            return _allow(self.name)
        return _deny(
            self.name,
            RejectReason.MISSING_DATA,
            f"no bar for {order.symbol} on {ctx.today}",
        )


class InvalidPriceRule:
    """Open price must be strictly positive."""

    name = "invalid_price"

    def check(self, order: Order, ctx: RuleCheckContext) -> Optional[RuleResult]:
        if ctx.proposed_price is not None and ctx.proposed_price > 0:
            return _allow(self.name)
        return _deny(
            self.name,
            RejectReason.INVALID_PRICE,
            f"open price {ctx.proposed_price} is invalid",
        )


class InsufficientCashRule:
    """BUY cost (price*quantity + commission + stamp_tax) <= available cash."""

    name = "insufficient_cash"

    def check(self, order: Order, ctx: RuleCheckContext) -> Optional[RuleResult]:
        if order.side is not Side.BUY:
            return _allow(self.name)
        if ctx.proposed_price is None:
            # price-side failure already caught upstream; let this pass.
            return _allow(self.name)
        cost = (
            Decimal(order.quantity) * ctx.proposed_price
            + ctx.estimated_commission
            + ctx.estimated_stamp_tax
        )
        if cost <= ctx.portfolio_cash:
            return _allow(self.name)
        return _deny(
            self.name,
            RejectReason.INSUFFICIENT_CASH,
            f"need {cost}, have {ctx.portfolio_cash}",
        )


class T1SellableRule:
    """SELL quantity must be <= position.sellable_quantity (T+1 rule)."""

    name = "t_plus_one"

    def check(self, order: Order, ctx: RuleCheckContext) -> Optional[RuleResult]:
        if order.side is not Side.SELL:
            return _allow(self.name)
        if order.quantity <= ctx.sellable_quantity:
            return _allow(self.name)
        return _deny(
            self.name,
            RejectReason.INSUFFICIENT_SHARES,
            f"sellable {ctx.sellable_quantity}, requested {order.quantity}",
        )


# --------------------------------------------------------------------- #
# Rule set
# --------------------------------------------------------------------- #


DEFAULT_V01_RULES: Sequence[Rule] = (
    LongOnlyRule(),
    LotSizeRule(),
    NonTradingDayRule(),
    InvalidPriceRule(),
    InsufficientCashRule(),
    T1SellableRule(),
)


class TradingRuleSet:
    """Ordered list of rules; the engine evaluates them per order.

    Evaluation is short-circuit: the first denial stops the chain and is
    returned (so only one rule shows up in the audit log per order).
    """

    def __init__(self, rules: Sequence[Rule] = DEFAULT_V01_RULES) -> None:
        self._rules: List[Rule] = list(rules)

    @property
    def rules(self) -> Sequence[Rule]:
        return tuple(self._rules)

    def evaluate(self, order: Order, ctx: RuleCheckContext) -> List[RuleResult]:
        """Run every rule and return the denials (allowing rules return None).

        Unlike `first_denial` this does NOT short-circuit: every rule runs,
        which is useful for diagnostics and tests. The engine's matching
        path uses `first_denial`.
        """
        return [
            result
            for result in (rule.check(order, ctx) for rule in self._rules)
            if result is not None
        ]

    def first_denial(self, order: Order, ctx: RuleCheckContext) -> Optional[RuleResult]:
        """Return the first denial or None if every rule passes."""
        for rule in self._rules:
            result = rule.check(order, ctx)
            if result is not None and not result.allowed:
                return result
        return None
