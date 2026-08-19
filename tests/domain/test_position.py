"""Hand-calculated tests for Position with T+1 rules."""

from decimal import Decimal

import pytest

from hqbacktest.domain.position import Position


def _empty() -> Position:
    return Position(symbol="600000.SH")


def test_initial_position_is_empty():
    p = _empty()
    assert p.quantity == 0
    assert p.sellable_quantity == 0
    assert p.avg_cost == Decimal(0)
    assert p.pending_today_buy == 0


def test_single_buy_sets_cost_and_pending_buy():
    p = _empty()
    p.update_buy(100, Decimal("10.00"))
    assert p.quantity == 100
    assert p.sellable_quantity == 0  # T+1: today's buy is not sellable
    assert p.avg_cost == Decimal("10.0000")
    assert p.pending_today_buy == 100


def test_two_buys_compute_weighted_avg_cost():
    p = _empty()
    p.update_buy(100, Decimal("10.00"))
    p.update_buy(100, Decimal("12.00"))
    assert p.quantity == 200
    assert p.avg_cost == Decimal("11.0000")  # (10*100 + 12*100)/200
    assert p.pending_today_buy == 200


def test_buy_quantizes_input_price():
    p = _empty()
    p.update_buy(100, Decimal("10.12345"))
    # ROUND_HALF_EVEN: kept digit is 4 (even) -> stays 4
    assert p.avg_cost == Decimal("10.1234")


def test_settle_t1_moves_pending_buy_to_sellable():
    p = _empty()
    p.update_buy(100, Decimal("10.00"))
    p.update_buy(100, Decimal("12.00"))
    p.settle_t1()
    assert p.sellable_quantity == 200
    assert p.pending_today_buy == 0


def test_sell_before_t1_settlement_is_rejected():
    p = _empty()
    p.update_buy(100, Decimal("10.00"))
    with pytest.raises(ValueError):
        p.update_sell(50, Decimal("11"))


def test_sell_after_settlement_with_profit_records_realized_pnl():
    p = _empty()
    p.update_buy(100, Decimal("10.00"))
    p.settle_t1()
    p.update_sell(100, Decimal("12.00"))
    assert p.quantity == 0
    assert p.sellable_quantity == 0
    assert p.avg_cost == Decimal(0)
    assert p.realized_pnl == Decimal("200.00")  # (12-10) * 100


def test_sell_after_settlement_with_loss_records_negative_pnl():
    p = _empty()
    p.update_buy(100, Decimal("10.00"))
    p.settle_t1()
    p.update_sell(100, Decimal("9.00"))
    assert p.realized_pnl == Decimal("-100.00")


def test_partial_sell_keeps_remaining_position():
    p = _empty()
    p.update_buy(200, Decimal("10.00"))
    p.settle_t1()
    p.update_sell(50, Decimal("11.00"))
    assert p.quantity == 150
    assert p.sellable_quantity == 150
    assert p.avg_cost == Decimal("10.0000")  # cost basis unchanged
    assert p.realized_pnl == Decimal("50.00")  # (11-10)*50


def test_sell_more_than_sellable_raises():
    p = _empty()
    p.update_buy(100, Decimal("10.00"))
    p.settle_t1()
    p.update_sell(50, Decimal("11.00"))
    with pytest.raises(ValueError):
        p.update_sell(60, Decimal("11.00"))  # only 50 left


def test_sell_quantizes_realized_pnl_to_cash():
    p = _empty()
    p.update_buy(100, Decimal("10.005"))
    p.settle_t1()
    p.update_sell(100, Decimal("12.005"))
    # (12.005 - 10.005) * 100 = 200.00 quantized to cash
    assert p.realized_pnl == Decimal("200.00")


def test_zero_position_market_value_is_zero():
    p = _empty()
    assert p.market_value(Decimal("99")) == Decimal(0)


def test_market_value_uses_quantized_price():
    p = _empty()
    p.update_buy(100, Decimal("10.00"))
    # ROUND_HALF_EVEN: 12.12345 -> 12.1234
    assert p.market_value(Decimal("12.12345")) == Decimal("1212.34")


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        _empty().update_buy(0, Decimal("10"))
    with pytest.raises(ValueError):
        _empty().update_buy(-1, Decimal("10"))
    with pytest.raises(ValueError):
        _empty().update_buy(100, 10.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _empty().update_sell(0, Decimal("10"))


def test_position_rejects_inconsistent_pending_buy_balance():
    with pytest.raises(ValueError, match="pending_today_buy"):
        Position(
            symbol="600000.SH",
            quantity=100,
            sellable_quantity=100,
            pending_today_buy=1,
        )
