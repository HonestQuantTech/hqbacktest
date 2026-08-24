"""Tests for the v0.1 cost model and its pluggability."""

from decimal import Decimal

import pytest

from hqbacktest.domain.enums import EventType, OrderType, Side
from hqbacktest.domain.order import Order
from hqbacktest.engine.cost_model import Cost, CostModel, DefaultCostModel


def _order(side=Side.BUY, qty=100, price=Decimal("10.00")) -> Order:
    return Order(
        order_id="O-test",
        symbol="600000.SH",
        side=side,
        quantity=qty,
        order_type=OrderType.MARKET,
        created_at="20240102",
        created_session=EventType.BAR_CLOSE,
    )


# --------------------------------------------------------------------- #
# Default cost model (v0.1 A-share fees)
# --------------------------------------------------------------------- #


def test_default_cost_uses_explicit_default_rates():
    model = DefaultCostModel()
    assert model.commission_rate == Decimal("0.00025")  # 0.025%
    assert model.min_commission == Decimal("5.00")
    assert model.stamp_tax_rate == Decimal("0.001")  # 0.1%
    assert model.transfer_fee_rate == Decimal("0.0")


def test_default_commission_uses_floor_when_proportional_is_too_low():
    """100 shares * 10 = 1000 CNY; 0.025% = 0.25 CNY < 5 CNY floor -> 5."""
    model = DefaultCostModel()
    cost = model.compute(_order(), Decimal("10.00"), 100)
    assert cost.commission == Decimal("5.00")


def test_default_commission_uses_proportional_when_above_floor():
    """100 shares * 1000 = 100000 CNY; 0.025% = 25 CNY > 5 floor -> 25."""
    model = DefaultCostModel()
    cost = model.compute(_order(qty=100), Decimal("1000.00"), 100)
    assert cost.commission == Decimal("25.00")


def test_stamp_tax_zero_on_buy():
    model = DefaultCostModel()
    cost = model.compute(_order(side=Side.BUY), Decimal("10.00"), 100)
    assert cost.stamp_tax == Decimal("0.00")


def test_stamp_tax_charged_on_sell():
    """100 * 10 = 1000; 0.1% = 1.00."""
    model = DefaultCostModel()
    cost = model.compute(_order(side=Side.SELL), Decimal("10.00"), 100)
    assert cost.stamp_tax == Decimal("1.00")


def test_transfer_fee_default_zero():
    model = DefaultCostModel()
    cost = model.compute(_order(), Decimal("10.00"), 100)
    assert cost.transfer_fee == Decimal("0.00")


def test_default_cost_quantizes_to_two_decimals():
    """Even when proportional yields an awkward fraction, fees stay
    quantized at 2 decimals (yen / jiao / fen)."""
    model = DefaultCostModel()
    cost = model.compute(_order(qty=137), Decimal("23.456"), 137)
    assert cost.commission == cost.commission.quantize(Decimal("0.01"))
    assert cost.stamp_tax == cost.stamp_tax.quantize(Decimal("0.01"))


def test_cost_rejects_non_positive_quantity():
    model = DefaultCostModel()
    with pytest.raises(ValueError):
        model.compute(_order(), Decimal("10"), 0)


def test_cost_rejects_non_positive_price():
    model = DefaultCostModel()
    with pytest.raises(ValueError):
        model.compute(_order(), Decimal("0"), 100)


def test_custom_commission_rate_overrides_default():
    model = DefaultCostModel(commission_rate=Decimal("0.01"))  # 1%
    cost = model.compute(_order(), Decimal("10.00"), 100)
    assert cost.commission == Decimal("10.00")  # 1000 * 0.01


def test_custom_min_commission_overrides_default():
    model = DefaultCostModel(min_commission=Decimal("20"))
    # 100 * 10 = 1000; 0.025% = 0.25; floor = 20 -> 20.
    cost = model.compute(_order(), Decimal("10.00"), 100)
    assert cost.commission == Decimal("20.00")


def test_zero_commission_and_stamp_tax_configurable():
    """User can disable commissions and stamp tax entirely."""
    model = DefaultCostModel(
        commission_rate=Decimal("0"),
        min_commission=Decimal("0"),
        stamp_tax_rate=Decimal("0"),
    )
    cost = model.compute(_order(side=Side.SELL), Decimal("10.00"), 100)
    assert cost.commission == Decimal("0.00")
    assert cost.stamp_tax == Decimal("0.00")
    assert cost.transfer_fee == Decimal("0.00")


# --------------------------------------------------------------------- #
# Custom cost model (protocol compliance)
# --------------------------------------------------------------------- #


def test_custom_cost_model_implements_protocol():
    class ZeroCommission:
        def compute(self, order, price, quantity):
            return Cost(
                commission=Decimal("0"),
                stamp_tax=Decimal("0"),
                transfer_fee=Decimal("0"),
            )

    model = ZeroCommission()
    assert isinstance(model, CostModel)
    cost = model.compute(_order(), Decimal("10.00"), 100)
    assert cost.commission == Decimal("0.00")


def test_pluggable_into_broker():
    """`SimulatedBroker` accepts a custom cost model and uses its fees."""
    from hqbacktest.engine.broker import SimulatedBroker

    class ZeroCommission:
        def compute(self, order, price, quantity):
            return Cost(
                commission=Decimal("0"),
                stamp_tax=Decimal("0"),
                transfer_fee=Decimal("0"),
            )

    broker = SimulatedBroker(cost_model=ZeroCommission())
    assert broker.cost_model() is not None
