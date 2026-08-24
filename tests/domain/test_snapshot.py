"""Tests for AccountSnapshot construction."""

from decimal import Decimal

import pytest

from hqbacktest.domain.snapshot import AccountSnapshot


def test_snapshot_aggregates_market_value():
    snap = AccountSnapshot.build(
        date="20240102",
        cash=Decimal("5000.00"),
        frozen_cash=Decimal("0"),
        realized_pnl=Decimal("100.00"),
        prices={"600000.SH": Decimal("12.00"), "000001.SZ": Decimal("20.00")},
        positions={
            "600000.SH": (100, 100, Decimal("10.0000")),
            "000001.SZ": (50, 50, Decimal("18.0000")),
        },
    )
    assert snap.cash == Decimal("5000.00")
    assert snap.market_value == Decimal("2200.00")  # 1200 + 1000
    assert snap.total_equity == Decimal("7200.00")
    assert snap.position_count == 2


def test_snapshot_skips_zero_quantity_positions():
    snap = AccountSnapshot.build(
        date="20240102",
        cash=Decimal("1000"),
        frozen_cash=Decimal("0"),
        realized_pnl=Decimal("0"),
        prices={"600000.SH": Decimal("12.00")},
        positions={"600000.SH": (0, 0, Decimal("0"))},
    )
    assert snap.position_count == 0
    assert snap.market_value == Decimal("0.00")


def test_snapshot_rejects_position_count_mismatch():
    with pytest.raises(ValueError):
        AccountSnapshot(
            date="20240102",
            cash=Decimal("0"),
            frozen_cash=Decimal("0"),
            market_value=Decimal("0"),
            total_equity=Decimal("0"),
            realized_pnl=Decimal("0"),
            position_count=2,
            positions=(),
        )


def test_snapshot_rejects_bad_date():
    with pytest.raises(ValueError):
        AccountSnapshot.build(
            date="2024-01-02",
            cash=Decimal("0"),
            frozen_cash=Decimal("0"),
            realized_pnl=Decimal("0"),
            prices={},
            positions={},
        )


def test_snapshot_rejects_missing_price_for_held_position():
    with pytest.raises(ValueError, match="missing market price"):
        AccountSnapshot.build(
            date="20240102",
            cash=Decimal("1000"),
            frozen_cash=Decimal("0"),
            realized_pnl=Decimal("0"),
            prices={},
            positions={"600000.SH": (100, 100, Decimal("10.0000"))},
        )
