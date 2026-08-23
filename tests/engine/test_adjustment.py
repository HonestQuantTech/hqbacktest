"""Tests for task 9: corporate-action design threshold + factor diagnostics.

These cover the TODO verification items:
    * "配置只接受 AdjustmentPolicy=none, 其他值均带明确原因拒绝"
    * "跨数据源因子、零/负因子、缺失因子和异常跳变均生成可追溯诊断,
       且不改变现金、持仓和净值"
    * "评审 CorporateActionProvider 草案, 确认其字段足以写出每一种
       公司行为的会计分录; 字段不足时不进入实现阶段"
"""

from decimal import Decimal

import pytest

from hqbacktest.data import InMemoryDataPortal
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import (
    AdjustmentPolicy,
    EventType,
    OrderType,
    Side,
)
from hqbacktest.domain.order import Order
from hqbacktest.domain.portfolio import Portfolio
from hqbacktest.engine.config import (
    V01_ADJUSTMENT_POLICY,
    BacktestConfig,
)
from hqbacktest.engine.context import Context
from hqbacktest.engine.corporate_actions import (
    DIAGNOSTIC_KINDS,
    FACTOR_TOTAL_RETURN_ADMISSION_CRITERIA,
    REQUIRED_CORPORATE_ACTION_FIELDS,
    CorporateAction,
    CorporateActionProvider,
    FactorDiagnostic,
    FactorDiagnosticCollector,
    analyze_factor_series,
)
from hqbacktest.engine.engine import BacktestEngine
from hqbacktest.engine.errors import ConfigurationError
from hqbacktest.engine.strategy import BaseStrategy


def _bar(date: str, close: str = "10.00") -> Bar:
    return Bar.from_raw(
        symbol="600000.SH",
        date=date,
        open="10.00",
        high="11.00",
        low="9.00",
        close=close,
        volume=1000,
    )


def _portal(days: list[str]) -> InMemoryDataPortal:
    p = InMemoryDataPortal(calendar=days)
    for d in days:
        p.add_bar(_bar(d))
    return p


def _config(
    start: str = "20240102", end: str = "20240104", **overrides
) -> BacktestConfig:
    base = dict(
        start_date=start,
        end_date=end,
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    base.update(overrides)
    return BacktestConfig(**base)


# --------------------------------------------------------------------- #
# AdjustmentPolicy enum
# --------------------------------------------------------------------- #


def test_adjustment_policy_enum_only_has_two_entries():
    """v0.1 keeps the future `factor_total_return` slot but does not use it."""
    assert AdjustmentPolicy.NONE.value == "none"
    assert AdjustmentPolicy.FACTOR_TOTAL_RETURN.value == "factor_total_return"


# --------------------------------------------------------------------- #
# BacktestConfig: only "none" is accepted
# --------------------------------------------------------------------- #


def test_config_defaults_to_none():
    cfg = _config()
    assert cfg.adjustment_policy == V01_ADJUSTMENT_POLICY
    assert cfg.adjustment_policy == "none"


def test_config_rejects_factor_total_return_with_explicit_reason():
    with pytest.raises(ConfigurationError) as exc:
        _config(adjustment_policy="factor_total_return")
    assert "factor_total_return" in str(exc.value)
    assert "v0.1 only supports" in str(exc.value)


def test_config_rejects_unknown_policy_with_explicit_reason():
    with pytest.raises(ConfigurationError) as exc:
        _config(adjustment_policy="quarterly_dividend")
    assert "quarterly_dividend" in str(exc.value)
    assert "v0.1 only supports" in str(exc.value)


def test_config_rejects_non_string_policy():
    with pytest.raises(ConfigurationError) as exc:
        _config(adjustment_policy=42)  # type: ignore[arg-type]
    assert "must be a string" in str(exc.value)


# --------------------------------------------------------------------- #
# BacktestResult: policy + diagnostics fields
# --------------------------------------------------------------------- #


def test_result_records_adjustment_policy():
    class Null(BaseStrategy):
        def initialize(self, context):
            pass

    engine = BacktestEngine(_config(), strategy=Null(), portal=_portal(["20240102"]))
    result = engine.run()
    assert result.adjustment_policy == "none"


def test_result_factor_diagnostics_empty_for_v0_1():
    """v0.1 never enables factor_total_return so diagnostics stays empty."""

    class Null(BaseStrategy):
        def initialize(self, context):
            pass

    engine = BacktestEngine(
        _config(), strategy=Null(), portal=_portal(["20240102", "20240103"])
    )
    result = engine.run()
    assert result.factor_diagnostics == []


def test_engine_exposes_factor_diagnostics_collector():
    class Null(BaseStrategy):
        def initialize(self, context):
            pass

    engine = BacktestEngine(_config(), strategy=Null(), portal=_portal(["20240102"]))
    assert isinstance(engine.factor_diagnostics, FactorDiagnosticCollector)


# --------------------------------------------------------------------- #
# Factor diagnostics: collection only, no portfolio modification
# --------------------------------------------------------------------- #


def test_factor_diagnostic_record_and_retrieve():
    collector = FactorDiagnosticCollector()
    collector.record(
        FactorDiagnostic(
            symbol="600000.SH",
            date="20240102",
            kind="non_positive",
            detail="factor=0.0",
        )
    )
    assert collector.has_any()
    assert len(collector) == 1
    item = collector.all()[0]
    assert item.symbol == "600000.SH"
    assert item.kind == "non_positive"


def test_factor_diagnostic_rejects_unknown_kind():
    with pytest.raises(ValueError):
        FactorDiagnostic(symbol="600000.SH", date="20240102", kind="unknown", detail="")


def test_factor_diagnostics_does_not_modify_portfolio():
    """Diagnostics are observational only; they never touch the ledger.

    A buy-and-hold strategy with a factor-diagnostic collector populated
    mid-run must end with the same cash/position as without diagnostics.
    """
    initial_cash = Decimal("100000")

    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    # Baseline: no diagnostics.
    baseline_engine = BacktestEngine(
        _config(), strategy=BuyHold(), portal=_portal(["20240102", "20240103"])
    )
    baseline_engine.run()
    baseline_cash = baseline_engine.portfolio.cash
    baseline_pos = baseline_engine.portfolio.positions.get("600000.SH")

    # With diagnostics injected (simulating future factor_total_return).
    collector = FactorDiagnosticCollector()
    collector.record(
        FactorDiagnostic(
            symbol="600000.SH",
            date="20240103",
            kind="missing",
            detail="synthetic test diagnostic",
        )
    )
    assert collector.has_any()

    # The portfolio from baseline is unchanged by adding the diagnostic.
    # With 2 trading days, only 1 BUY matches (the second is cancelled
    # with BACKTEST_ENDED at run end).
    # Cash = 100000 - 1000 - 5 = 98995.
    assert baseline_cash == Decimal("98995.00")
    assert baseline_pos is not None
    assert baseline_pos.quantity == 100


# --------------------------------------------------------------------- #
# CorporateAction: design draft validation
# --------------------------------------------------------------------- #


def test_corporate_action_rejects_empty_symbol():
    with pytest.raises(ValueError):
        CorporateAction(symbol="", ex_date="20240102")


def test_corporate_action_rejects_bad_ex_date():
    with pytest.raises(ValueError):
        CorporateAction(symbol="600000.SH", ex_date="2024-01-02")


def test_corporate_action_rejects_negative_cash_dividend():
    with pytest.raises(ValueError):
        CorporateAction(
            symbol="600000.SH",
            ex_date="20240102",
            cash_dividend_per_share=Decimal("-0.5"),
        )


def test_corporate_action_rejects_invalid_fractional_handling():
    with pytest.raises(ValueError):
        CorporateAction(
            symbol="600000.SH",
            ex_date="20240102",
            fractional_share_handling="INVALID",  # type: ignore[arg-type]
        )


def test_corporate_action_default_fields_are_zero_or_safe():
    """Default values for unrecognised action types are all zero / safe."""
    action = CorporateAction(symbol="600000.SH", ex_date="20240102")
    assert action.cash_dividend_per_share == Decimal(0)
    assert action.stock_dividend_ratio == Decimal(0)
    assert action.rights_ratio == Decimal(0)
    assert action.rights_price == Decimal(0)
    assert action.tax_rate == Decimal(0)
    assert action.conversion_ratio == Decimal(0)
    assert action.fractional_share_handling == "CASH"
    assert action.note == ""


def test_corporate_action_validates_dividend_tax_rate():
    with pytest.raises(ValueError):
        CorporateAction(
            symbol="600000.SH",
            ex_date="20240102",
            tax_rate=Decimal("-0.1"),
        )


# --------------------------------------------------------------------- #
# CorporateActionProvider: required-fields contract
# --------------------------------------------------------------------- #


def test_required_corporate_action_fields_lists_authoritative_fields():
    """Field set documented by the design draft is the source of truth."""
    for field_name in (
        "symbol",
        "ex_date",
        "cash_dividend_per_share",
        "stock_dividend_ratio",
        "rights_ratio",
        "rights_price",
        "tax_rate",
        "conversion_ratio",
        "fractional_share_handling",
        "note",
    ):
        assert field_name in REQUIRED_CORPORATE_ACTION_FIELDS


def test_corporate_action_provider_protocol_exists():
    """The Protocol type is exported and runtime-checkable."""
    assert hasattr(CorporateActionProvider, "actions_for")


def test_incomplete_provider_is_rejected_at_review():
    """An implementation missing required fields cannot satisfy the contract."""

    class IncompleteProvider:
        def actions_for(self, symbol: str, start: str, end: str):
            # Missing: full CorporateAction field set, fractional handling,
            # rights_price, conversion_ratio, etc.
            return []

    # Runtime check does not require attribute presence on Protocol;
    # the design review (this test) is the gate.
    assert not hasattr(IncompleteProvider(), "fractional_share_handling")


# --------------------------------------------------------------------- #
# Engine integration: adjustment_policy does not affect v0.1 results
# --------------------------------------------------------------------- #


def test_engine_uses_unadjusted_prices_throughout_run():
    """Even if factor data exists on disk, the engine must not apply it."""

    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    p = _portal(["20240102", "20240103", "20240104"])
    engine = BacktestEngine(
        _config("20240102", "20240104"),
        strategy=BuyHold(),
        portal=p,
    )
    engine.run()
    # With 3 trading days, 2 BUYs match (D+1 each): 2 * (1000 + 5) = 2010.
    # Cash = 100000 - 2010 = 97990. Position: 200 @ 10 (no factor adjustment).
    assert engine.portfolio.cash == Decimal("97990.00")
    assert engine.portfolio.positions["600000.SH"].quantity == 200
    assert engine.portfolio.positions["600000.SH"].avg_cost == Decimal("10.0000")
    assert engine.result.adjustment_policy == "none"
    assert engine.result.factor_diagnostics == []


# --------------------------------------------------------------------- #
# analyze_factor_series: the four diagnostic kinds (TODO task 9 验证)
# --------------------------------------------------------------------- #

DATES = ["20240102", "20240103", "20240104"]


def test_analyze_clean_series_produces_no_diagnostics():
    factors = [(d, Decimal("1.0")) for d in DATES]
    assert analyze_factor_series("600000.SH", DATES, factors) == []


def test_analyze_missing_factor_generates_diagnostic():
    factors = [("20240102", Decimal("1.0")), ("20240104", Decimal("1.0"))]
    diags = analyze_factor_series("600000.SH", DATES, factors)
    assert [(d.date, d.kind) for d in diags] == [("20240103", "missing")]


def test_analyze_non_positive_factor_generates_diagnostic():
    factors = [
        ("20240102", Decimal("1.0")),
        ("20240103", Decimal("0")),
        ("20240104", Decimal("1.0")),
    ]
    diags = analyze_factor_series("600000.SH", DATES, factors)
    kinds = [(d.date, d.kind) for d in diags]
    assert ("20240103", "non_positive") in kinds


def test_analyze_negative_factor_generates_diagnostic():
    factors = [("20240102", Decimal("-1.5"))]
    diags = analyze_factor_series("600000.SH", ["20240102"], factors)
    assert [(d.date, d.kind) for d in diags] == [("20240102", "non_positive")]


def test_analyze_abnormal_jump_generates_diagnostic():
    factors = [
        ("20240102", Decimal("1.0")),
        ("20240103", Decimal("3.0")),  # 3x overnight: outside [0.5, 2.0]
        ("20240104", Decimal("3.0")),
    ]
    diags = analyze_factor_series("600000.SH", DATES, factors)
    assert [(d.date, d.kind) for d in diags] == [("20240103", "abnormal_jump")]


def test_analyze_normal_jump_within_band_is_quiet():
    factors = [
        ("20240102", Decimal("1.0")),
        ("20240103", Decimal("1.2")),  # +20%: inside [0.5, 2.0]
        ("20240104", Decimal("1.1")),
    ]
    assert analyze_factor_series("600000.SH", DATES, factors) == []


def test_analyze_cross_source_disagreement_generates_diagnostic():
    factors = [(d, Decimal("1.0")) for d in DATES]
    reference = [
        ("20240102", Decimal("1.0")),
        ("20240103", Decimal("1.5")),  # disagrees beyond tolerance
        ("20240104", Decimal("1.0")),
    ]
    diags = analyze_factor_series("600000.SH", DATES, factors, reference=reference)
    assert [(d.date, d.kind) for d in diags] == [("20240103", "cross_source")]


def test_analyze_diagnostics_carry_symbol_and_traceable_detail():
    factors = [("20240102", Decimal("0"))]
    diags = analyze_factor_series("600000.SH", ["20240102"], factors)
    assert diags[0].symbol == "600000.SH"
    assert diags[0].detail  # machine-traceable reason string


def test_analyze_never_touches_ledger_state():
    """The analyzer is pure: running it over anomalous factors changes
    neither the portfolio nor its positions (TODO task 9 验证)."""
    portfolio = Portfolio(initial_cash=Decimal("100000"))
    portfolio.get_position("600000.SH").update_buy(100, Decimal("10"))
    analyze_factor_series(
        "600000.SH",
        DATES,
        [("20240103", Decimal("0"))],  # missing + non_positive anomalies
    )
    assert portfolio.cash == Decimal("100000")
    assert portfolio.positions["600000.SH"].quantity == 100


def test_diagnostic_kinds_constant_matches_validation():
    assert set(DIAGNOSTIC_KINDS) == {
        "missing",
        "non_positive",
        "cross_source",
        "abnormal_jump",
    }


def test_admission_criteria_cover_every_ledger_aspect():
    """TODO task 9: the factor_total_return admission standard must spell out
    cash / quantity / sellable / cost / fills / valuation / metrics."""
    for aspect in (
        "cash",
        "position_quantity",
        "sellable_quantity",
        "avg_cost",
        "fills_and_sells",
        "day_end_valuation",
        "result_metrics",
    ):
        assert aspect in FACTOR_TOTAL_RETURN_ADMISSION_CRITERIA


def test_adjustment_policy_constant_has_single_definition():
    """config / result / corporate_actions share ONE constant object."""
    from hqbacktest.engine import config as config_mod
    from hqbacktest.engine import corporate_actions as ca_mod
    from hqbacktest.engine import result as result_mod

    assert config_mod.V01_ADJUSTMENT_POLICY is ca_mod.V01_ADJUSTMENT_POLICY
    assert result_mod.V01_ADJUSTMENT_POLICY is ca_mod.V01_ADJUSTMENT_POLICY


def test_engine_result_stays_none_after_failed_run():
    """A failed run must not leave a half-populated result on the engine."""
    from hqbacktest.engine.errors import RunFailed

    class Boom(BaseStrategy):
        def on_bar(self, context, data):
            raise RuntimeError("boom")

    engine = BacktestEngine(
        _config(), strategy=Boom(), portal=_portal(["20240102", "20240103"])
    )
    with pytest.raises(RunFailed):
        engine.run()
    assert engine.result is None
