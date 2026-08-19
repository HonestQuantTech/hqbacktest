"""Tests for the v0.1 CorporateActionAdjustment placeholder."""

from decimal import Decimal

import pytest

from hqbacktest.domain.adjustment import CorporateActionAdjustment


def test_noop_factory_keeps_factor_at_one():
    adj = CorporateActionAdjustment.noop("600000.SH", "20240102")
    assert adj.is_noop()
    assert adj.factor_ratio == Decimal(1)
    assert adj.cash_per_share == Decimal(0)
    assert adj.action_type == "NONE"


def test_adjustment_validates_date_format():
    with pytest.raises(ValueError):
        CorporateActionAdjustment(
            symbol="600000.SH",
            ex_date="2024-01-02",
            action_type="DIVIDEND",
        )


def test_adjustment_rejects_non_positive_factor():
    with pytest.raises(ValueError):
        CorporateActionAdjustment(
            symbol="600000.SH",
            ex_date="20240102",
            action_type="SPLIT",
            factor_ratio=Decimal(0),
        )
    with pytest.raises(ValueError):
        CorporateActionAdjustment(
            symbol="600000.SH",
            ex_date="20240102",
            action_type="SPLIT",
            factor_ratio=Decimal(-1),
        )


def test_is_noop_returns_false_for_dividend():
    adj = CorporateActionAdjustment(
        symbol="600000.SH",
        ex_date="20240102",
        action_type="DIVIDEND",
        cash_per_share=Decimal("0.50"),
    )
    assert not adj.is_noop()
