"""Round-trip JSON serialization tests for domain models."""

from dataclasses import dataclass
from decimal import Decimal

from hqbacktest.domain.adjustment import CorporateActionAdjustment
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import (
    EventType,
    OrderStatus,
    OrderType,
    RejectReason,
    Side,
)
from hqbacktest.domain.fill import Fill
from hqbacktest.domain.order import Order
from hqbacktest.domain.portfolio import Portfolio
from hqbacktest.domain.position import Position
from hqbacktest.domain.serialization import dump_json, dump_jsonl, to_jsonable
from hqbacktest.domain.snapshot import AccountSnapshot


def test_decimal_serializes_as_string():
    assert to_jsonable(Decimal("1.2345")) == "1.2345"


def test_enum_serializes_by_name():
    assert to_jsonable(Side.BUY) == "BUY"
    assert to_jsonable(OrderStatus.FILLED) == "FILLED"
    assert to_jsonable(EventType.OPEN_MATCH) == "OPEN_MATCH"


def test_dataclass_recurses():
    bar = Bar.from_raw(
        symbol="600000.SH",
        date="20240102",
        open="10.00",
        high="10.50",
        low="9.90",
        close="10.20",
        volume=1000,
    )
    payload = to_jsonable(bar)
    assert payload["symbol"] == "600000.SH"
    assert payload["open"] == "10.0000"
    assert payload["close"] == "10.2000"
    assert payload["volume"] == 1000


def test_order_round_trip_json():
    o = Order(
        order_id="O001",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=100,
        order_type=OrderType.MARKET,
        created_at="20240102",
        created_session=EventType.BEFORE_TRADING_START,
    )
    o.transition(OrderStatus.ACCEPTED, at="20240102")
    o.transition(OrderStatus.PENDING, at="20240102")
    o.record_fill("F001", 100, Decimal("10.50"), at="20240103")

    payload = to_jsonable(o)
    assert payload["order_id"] == "O001"
    assert payload["side"] == "BUY"
    assert payload["status"] == "FILLED"
    assert payload["filled_quantity"] == 100
    assert payload["avg_fill_price"] == "10.5000"
    assert payload["fill_ids"] == ["F001"]
    assert payload["accepted_at"] == "20240102"
    assert payload["pending_at"] == "20240102"
    assert payload["filled_at"] == "20240103"

    # dump_json must not raise
    rendered = dump_json(o)
    assert "FILLED" in rendered
    assert "10.5000" in rendered


def test_fill_serializes_fees():
    fill = Fill.from_trade(
        fill_id="F001",
        order_id="O001",
        symbol="600000.SH",
        side=Side.BUY,
        quantity=100,
        price=Decimal("12.50"),
        commission=Decimal("5.00"),
        stamp_tax=Decimal("0"),
        other_fee=Decimal("0"),
        filled_at="20240103",
        session=EventType.OPEN_MATCH,
    )
    payload = to_jsonable(fill)
    assert payload["amount"] == "1250.00"
    assert payload["commission"] == "5.00"
    assert payload["side"] == "BUY"


def test_position_serializes_in_decimal_strings():
    p = Position(
        symbol="600000.SH",
        quantity=100,
        sellable_quantity=100,
        avg_cost=Decimal("10.0000"),
    )
    payload = to_jsonable(p)
    assert payload["quantity"] == 100
    assert payload["avg_cost"] == "10.0000"


def test_portfolio_serializes_positions_map():
    pf = Portfolio(initial_cash=Decimal("10000.00"))
    payload = to_jsonable(pf)
    assert payload["initial_cash"] == "10000.00"
    assert payload["cash"] == "10000.00"
    assert isinstance(payload["positions"], dict)


def test_adjustment_serializes():
    adj = CorporateActionAdjustment.noop("600000.SH", "20240102")
    payload = to_jsonable(adj)
    assert payload["symbol"] == "600000.SH"
    assert payload["action_type"] == "NONE"
    assert payload["factor_ratio"] == "1"


def test_snapshot_serializes_tuple_of_positions():
    snap = AccountSnapshot.build(
        date="20240102",
        cash=Decimal("5000.00"),
        frozen_cash=Decimal("0"),
        realized_pnl=Decimal("0"),
        prices={"600000.SH": Decimal("12.00")},
        positions={"600000.SH": (100, 100, Decimal("10.0000"))},
    )
    payload = to_jsonable(snap)
    assert payload["date"] == "20240102"
    assert payload["cash"] == "5000.00"
    assert payload["position_count"] == 1
    assert payload["positions"][0]["symbol"] == "600000.SH"
    assert payload["positions"][0]["market_value"] == "1200.00"


def test_dump_jsonl_emits_one_object_per_line():
    fills = [
        Fill.from_trade(
            fill_id=f"F{i:03d}",
            order_id=f"O{i:03d}",
            symbol="600000.SH",
            side=Side.BUY,
            quantity=100,
            price=Decimal("10.00"),
            commission=Decimal("5.00"),
            stamp_tax=Decimal("0"),
            other_fee=Decimal("0"),
            filled_at="20240102",
            session=EventType.OPEN_MATCH,
        )
        for i in range(3)
    ]
    rendered = dump_jsonl(fills)
    lines = rendered.split("\n")
    assert len(lines) == 3
    assert "F000" in lines[0]
    assert "F001" in lines[1]
    assert "F002" in lines[2]
    assert '"BUY"' in lines[0]


def test_unknown_type_raises():
    class Mystery:
        pass

    with __import__("pytest").raises(TypeError):
        to_jsonable(Mystery())
