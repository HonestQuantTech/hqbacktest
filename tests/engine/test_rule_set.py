"""Tests for the v0.1 TradingRuleSet and its individual rules."""

from decimal import Decimal

import pytest

from hqbacktest.domain.enums import (
    EventType,
    OrderStatus,
    OrderType,
    RejectReason,
    Side,
)
from hqbacktest.domain.order import Order
from hqbacktest.engine.rule_set import (
    DEFAULT_V01_RULES,
    InsufficientCashRule,
    InvalidPriceRule,
    LotSizeRule,
    LongOnlyRule,
    NonTradingDayRule,
    RuleCheckContext,
    RuleResult,
    T1SellableRule,
    TradingRuleSet,
)


def _order(
    symbol="600000.SH",
    side=Side.BUY,
    qty=100,
    created_at="20240102",
    created_session=EventType.BAR_CLOSE,
) -> Order:
    return Order(
        order_id="O-test",
        symbol=symbol,
        side=side,
        quantity=qty,
        order_type=OrderType.MARKET,
        created_at=created_at,
        created_session=created_session,
    )


def _ctx(
    *, price=Decimal("10.00"), sellable=0, cash=Decimal("100000"), bar=True
) -> RuleCheckContext:
    return RuleCheckContext(
        today="20240102",
        proposed_price=price,
        proposed_quantity=100,
        sellable_quantity=sellable,
        portfolio_cash=cash,
        bar_available=bar,
    )


# --------------------------------------------------------------------- #
# Individual rules
# --------------------------------------------------------------------- #


def test_long_only_allows_buy_and_sell():
    rule = LongOnlyRule()
    assert rule.check(_order(side=Side.BUY), _ctx()) is None
    assert rule.check(_order(side=Side.SELL), _ctx()) is None


def test_lot_size_passes_100_shares():
    rule = LotSizeRule()
    assert rule.check(_order(qty=100), _ctx()) is None


def test_lot_size_rejects_150_shares():
    rule = LotSizeRule()
    result = rule.check(_order(qty=150), _ctx())
    assert result is not None
    assert not result.allowed
    assert result.rule_name == "lot_size"
    assert "150" in result.detail


def test_lot_size_allows_odd_lot_sell():
    """A-share rules: 买入整手, but odd-lot SELLs are allowed (零股卖出)."""
    rule = LotSizeRule()
    assert rule.check(_order(side=Side.SELL, qty=150), _ctx(sellable=200)) is None
    assert rule.check(_order(side=Side.SELL, qty=50), _ctx(sellable=100)) is None


def test_lot_size_custom_lot():
    rule = LotSizeRule(lot_size=10)
    assert rule.check(_order(qty=10), _ctx()) is None
    assert rule.check(_order(qty=15), _ctx()) is not None


def test_lot_size_rejects_non_positive_lot_size():
    with pytest.raises(ValueError):
        LotSizeRule(lot_size=0)


def test_non_trading_day_passes_when_bar_present():
    rule = NonTradingDayRule()
    assert rule.check(_order(), _ctx(bar=True)) is None


def test_non_trading_day_rejects_when_bar_missing():
    rule = NonTradingDayRule()
    result = rule.check(_order(), _ctx(bar=False))
    assert result is not None
    assert result.reason is RejectReason.MISSING_DATA


def test_invalid_price_passes_for_positive_price():
    rule = InvalidPriceRule()
    assert rule.check(_order(), _ctx(price=Decimal("10"))) is None


def test_invalid_price_rejects_zero_or_negative_or_none():
    rule = InvalidPriceRule()
    for bad in (Decimal("0"), Decimal("-1"), None):
        result = rule.check(_order(), _ctx(price=bad))
        assert result is not None
        assert result.reason is RejectReason.INVALID_PRICE


def test_insufficient_cash_only_checks_buy():
    rule = InsufficientCashRule()
    # SELL: rule always passes (cash side).
    assert rule.check(_order(side=Side.SELL), _ctx(price=Decimal("100"))) is None


def test_insufficient_cash_passes_when_cash_sufficient():
    rule = InsufficientCashRule()
    assert rule.check(_order(qty=100, side=Side.BUY), _ctx(price=Decimal("10"))) is None


def test_insufficient_cash_rejects_when_cash_too_low():
    rule = InsufficientCashRule()
    # 100 shares * 100 CNY = 10000, available cash = 1000.
    result = rule.check(
        _order(qty=100, side=Side.BUY), _ctx(price=Decimal("100"), cash=Decimal("1000"))
    )
    assert result is not None
    assert result.reason is RejectReason.INSUFFICIENT_CASH


def test_insufficient_cash_skipped_when_no_price():
    """No bar / no price means price-side rule fires first; cash rule passes."""
    rule = InsufficientCashRule()
    assert rule.check(_order(side=Side.BUY), _ctx(price=None)) is None


def test_t1_sellable_allows_when_quantity_ok():
    rule = T1SellableRule()
    assert rule.check(_order(side=Side.SELL, qty=100), _ctx(sellable=200)) is None


def test_t1_sellable_rejects_overflow():
    rule = T1SellableRule()
    result = rule.check(_order(side=Side.SELL, qty=200), _ctx(sellable=100))
    assert result is not None
    assert result.reason is RejectReason.INSUFFICIENT_SHARES
    assert "100" in result.detail


def test_t1_sellable_only_checks_sell():
    rule = T1SellableRule()
    assert rule.check(_order(side=Side.BUY, qty=10000), _ctx(sellable=0)) is None


# --------------------------------------------------------------------- #
# Rule set composition
# --------------------------------------------------------------------- #


def test_default_rule_set_order():
    rule_names = [type(r).__name__ for r in DEFAULT_V01_RULES]
    assert rule_names == [
        "LongOnlyRule",
        "LotSizeRule",
        "NonTradingDayRule",
        "InvalidPriceRule",
        "InsufficientCashRule",
        "T1SellableRule",
    ]


def test_trading_rule_set_evaluate_returns_per_rule_results():
    rs = TradingRuleSet()  # default rules
    order = _order(qty=100, side=Side.BUY)
    ctx = _ctx(price=Decimal("10"), bar=True, cash=Decimal("100000"))
    results = rs.evaluate(order, ctx)
    # All rules pass; no results returned (allowed = None).
    assert results == []


def test_trading_rule_set_first_denial_short_circuits():
    """Bad side fails LongOnly, no later rule fires."""
    rs = TradingRuleSet()
    order = _order(side=Side.BUY)  # side=Side.BUY passes LongOnly
    # Force a downstream failure: invalid lot + insufficient cash.
    bad_order = Order(
        order_id="O-bad",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=99,  # not a lot of 100
        order_type=OrderType.MARKET,
        created_at="20240102",
        created_session=EventType.BAR_CLOSE,
    )
    first = rs.first_denial(bad_order, _ctx(price=Decimal("10")))
    assert first is not None
    assert first.rule_name == "lot_size"


def test_trading_rule_set_evaluate_records_all_results():
    """`evaluate` runs every rule; only the first denial appears if
    `first_denial` is used."""
    rs = TradingRuleSet()
    bad = Order(
        order_id="O-bad",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=99,  # not a lot
        order_type=OrderType.MARKET,
        created_at="20240102",
        created_session=EventType.BAR_CLOSE,
    )
    results = rs.evaluate(bad, _ctx(price=Decimal("10")))
    assert any(r.rule_name == "lot_size" for r in results)


def test_trading_rule_set_customizable():
    """Tasks 8 can override or extend the default rule set."""
    rs = TradingRuleSet(rules=[LongOnlyRule()])
    # Lot size not enforced: 99 shares pass through.
    bad = Order(
        order_id="O-bad",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=99,
        order_type=OrderType.MARKET,
        created_at="20240102",
        created_session=EventType.BAR_CLOSE,
    )
    assert rs.first_denial(bad, _ctx(price=Decimal("10"))) is None


def test_rule_result_dataclass_fields():
    r = RuleResult(
        allowed=False,
        rule_name="lot_size",
        reason=RejectReason.OTHER,
        detail="quantity 99 not a multiple of 100",
    )
    assert r.allowed is False
    assert r.rule_name == "lot_size"
    assert r.reason is RejectReason.OTHER
    assert "99" in r.detail
