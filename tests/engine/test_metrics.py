"""Tests for `engine/metrics.py` (task 10 verification).

Hand-calculated values used here:
    * 100 -> 110 -> 121 -> 110 over 3 days gives:
        - total_return = 0.10
        - daily_returns = [0, 0.10, 0.10, -0.0909...]
        - drawdown peaks at 121, then 110 -> 11/121 ≈ 0.0909
"""

from decimal import Decimal

import pytest

from hqbacktest.domain.fill import Fill
from hqbacktest.domain.enums import EventType, Side
from hqbacktest.engine.metrics import (
    EquityPoint,
    MetricsConfig,
    PerformanceMetrics,
    compute_metrics,
)


def _fill(side: Side, qty: int, price: str, comm: str = "0", stamp: str = "0") -> Fill:
    amount = (
        Decimal(qty) * Decimal(price)
        if side is Side.BUY
        else -Decimal(qty) * Decimal(price)
    )
    return Fill(
        fill_id=f"F{side.name}{qty}{price}",
        order_id="O-test",
        symbol="600000.SH",
        side=side,
        quantity=qty,
        price=Decimal(price),
        amount=amount,
        commission=Decimal(comm),
        stamp_tax=Decimal(stamp),
        other_fee=Decimal("0"),
        filled_at="20240102",
        session=EventType.OPEN_MATCH,
    )


# --------------------------------------------------------------------- #
# MetricsConfig validation
# --------------------------------------------------------------------- #


def test_metrics_config_defaults_to_zero_risk_free_and_252_days():
    cfg = MetricsConfig()
    assert cfg.risk_free_rate == Decimal("0.0")
    assert cfg.annual_trading_days == 252


def test_metrics_config_rejects_non_positive_annual_days():
    with pytest.raises(ValueError):
        MetricsConfig(annual_trading_days=0)


def test_metrics_config_rejects_non_decimal_risk_free():
    with pytest.raises(TypeError):
        MetricsConfig(risk_free_rate=0.025)  # type: ignore[arg-type]


# --------------------------------------------------------------------- #
# Empty / single-day / flat-equity edge cases
# --------------------------------------------------------------------- #


def test_empty_run_returns_zero_or_none_metrics():
    cfg = MetricsConfig()
    m = compute_metrics(
        equity_curve=[],
        fills=[],
        initial_cash=Decimal("100000"),
        config=cfg,
    )
    assert m.total_return == Decimal("0")
    assert m.annualized_return is None
    assert m.daily_volatility is None
    assert m.sharpe_ratio is None
    assert m.max_drawdown == Decimal("0")
    assert m.trade_count == 0
    assert m.win_rate is None
    assert any("no trading days" in n for n in m.notes)


def test_single_day_run_only_computes_total_return():
    eq = [
        EquityPoint(
            date="20240102",
            cash=Decimal("90000"),
            market_value=Decimal("10000"),
            total_equity=Decimal("100000"),
            daily_return=Decimal("0"),
            drawdown=Decimal("0"),
        )
    ]
    m = compute_metrics(
        equity_curve=eq,
        fills=[],
        initial_cash=Decimal("100000"),
        config=MetricsConfig(),
    )
    assert m.total_return == Decimal("0")
    assert m.annualized_return is None
    assert m.sharpe_ratio is None


def test_flat_equity_gives_zero_volatility_and_no_sharpe():
    eq = [
        EquityPoint(
            "20240102",
            Decimal("100000"),
            Decimal("0"),
            Decimal("100000"),
            Decimal("0"),
            Decimal("0"),
        ),
        EquityPoint(
            "20240103",
            Decimal("100000"),
            Decimal("0"),
            Decimal("100000"),
            Decimal("0"),
            Decimal("0"),
        ),
        EquityPoint(
            "20240104",
            Decimal("100000"),
            Decimal("0"),
            Decimal("100000"),
            Decimal("0"),
            Decimal("0"),
        ),
    ]
    m = compute_metrics(
        equity_curve=eq,
        fills=[],
        initial_cash=Decimal("100000"),
        config=MetricsConfig(),
    )
    assert m.daily_volatility == Decimal("0")
    assert m.sharpe_ratio is None
    assert any("zero volatility" in n for n in m.notes)


# --------------------------------------------------------------------- #
# Hand-calculated happy path
# --------------------------------------------------------------------- #


def test_three_day_equity_computes_total_and_drawdown():
    eq = [
        EquityPoint(
            "20240102",
            Decimal("90000"),
            Decimal("0"),
            Decimal("100000"),
            Decimal("0"),
            Decimal("0"),
        ),
        EquityPoint(
            "20240103",
            Decimal("80000"),
            Decimal("0"),
            Decimal("110000"),
            Decimal("0.10"),
            Decimal("0"),
        ),
        EquityPoint(
            "20240104",
            Decimal("70000"),
            Decimal("0"),
            Decimal("121000"),
            Decimal("0.10"),
            Decimal("0"),
        ),
        EquityPoint(
            "20240105",
            Decimal("80000"),
            Decimal("0"),
            Decimal("110000"),
            Decimal("-0.0909"),
            Decimal("0.0909"),
        ),
    ]
    m = compute_metrics(
        equity_curve=eq,
        fills=[],
        initial_cash=Decimal("100000"),
        config=MetricsConfig(),
    )
    # total_return = 110000/100000 - 1 = 0.10
    assert m.total_return.quantize(Decimal("0.01")) == Decimal("0.10")
    # max drawdown = (121000 - 110000) / 121000 = 0.0909
    assert m.max_drawdown.quantize(Decimal("0.001")) == Decimal("0.091")


def test_annualized_return_with_252_trading_days():
    eq = [
        EquityPoint(
            f"2024{i:02d}02",
            Decimal("100000"),
            Decimal("0"),
            Decimal("100000"),
            Decimal("0"),
            Decimal("0"),
        )
        for i in range(1, 11)
    ]
    # Make equity grow to 110000 over 10 days.
    eq[-1] = EquityPoint(
        "20241002",
        Decimal("90000"),
        Decimal("0"),
        Decimal("110000"),
        Decimal("0.10"),
        Decimal("0"),
    )
    m = compute_metrics(
        equity_curve=eq,
        fills=[],
        initial_cash=Decimal("100000"),
        config=MetricsConfig(annual_trading_days=252),
    )
    # (1.1) ** (10 / 252) - 1 = 1.00384... - 1 ≈ 0.00384
    assert m.annualized_return is not None
    assert m.annualized_return.quantize(Decimal("0.0001")) == Decimal("0.0038")


def test_turnover_is_one_sided_average_over_initial_cash():
    fills = [
        _fill(Side.SELL, 100, "12.00"),
    ]
    m = compute_metrics(
        equity_curve=[],
        fills=fills,
        initial_cash=Decimal("10000"),
        config=MetricsConfig(),
    )
    # (buy 0 + sell 1200) / 2 / 10000 = 0.06
    assert m.turnover == Decimal("0.06")
    assert m.trade_count == 1


def test_turnover_counts_buys_and_sells_symmetrically():
    """A buy-only run must have non-zero turnover (one-sided average)."""
    fills = [
        _fill(Side.BUY, 100, "12.00"),
    ]
    m = compute_metrics(
        equity_curve=[],
        fills=fills,
        initial_cash=Decimal("10000"),
        config=MetricsConfig(),
    )
    # (buy 1200 + sell 0) / 2 / 10000 = 0.06
    assert m.turnover == Decimal("0.06")


def test_win_rate_is_ratio_of_profitable_sells_to_total_sells():
    # Build a small sequence: BUY 100 @ 10, SELL 100 @ 12 (win), SELL 100 @ 8
    # (loss). Basis tracks the running average.
    fills = [
        _fill(Side.BUY, 100, "10.00"),
        _fill(Side.SELL, 100, "12.00"),  # win (12 > 10)
        _fill(Side.SELL, 100, "8.00"),  # loss (8 < 10)
    ]
    m = compute_metrics(
        equity_curve=[],
        fills=fills,
        initial_cash=Decimal("10000"),
        config=MetricsConfig(),
    )
    assert m.win_rate == Decimal("0.5")  # 1 win / 2 sells
    assert m.trade_count == 3


def test_win_rate_ignores_buys_after_the_sell():
    """Chronological replay: a BUY that happens AFTER a SELL must not
    change that SELL's win/loss basis."""
    fills = [
        _fill(Side.BUY, 100, "10.00"),
        _fill(Side.SELL, 100, "9.00"),  # loss: 9 < 10 (basis at sell time)
        _fill(Side.BUY, 100, "1.00"),  # later cheap buy must not matter
    ]
    m = compute_metrics(
        equity_curve=[],
        fills=fills,
        initial_cash=Decimal("10000"),
        config=MetricsConfig(),
    )
    # If the later BUY leaked into the basis, avg cost would be 5.5 and
    # the sell at 9 would wrongly count as a win.
    assert m.win_rate == Decimal("0")  # 0 wins / 1 sell


def test_no_sell_fills_leaves_win_rate_as_none():
    fills = [_fill(Side.BUY, 100, "10.00")]
    m = compute_metrics(
        equity_curve=[],
        fills=fills,
        initial_cash=Decimal("10000"),
        config=MetricsConfig(),
    )
    assert m.win_rate is None


def test_no_fills_records_trade_count_zero_and_turnover_zero():
    m = compute_metrics(
        equity_curve=[],
        fills=[],
        initial_cash=Decimal("10000"),
        config=MetricsConfig(),
    )
    assert m.trade_count == 0
    assert m.turnover == Decimal("0")


# --------------------------------------------------------------------- #
# Sharpe ratio with risk-free rate
# --------------------------------------------------------------------- #


def test_sharpe_with_risk_free_rate_and_simple_equity():
    # Two days: equity 100 -> 110. Daily return 0.10.
    # daily_volatility = stdev([0.10]) = 0 (single value)
    # -> sharpe None.
    eq = [
        EquityPoint(
            "20240102",
            Decimal("100000"),
            Decimal("0"),
            Decimal("100000"),
            Decimal("0"),
            Decimal("0"),
        ),
        EquityPoint(
            "20240103",
            Decimal("90000"),
            Decimal("0"),
            Decimal("110000"),
            Decimal("0.10"),
            Decimal("0"),
        ),
    ]
    cfg = MetricsConfig(risk_free_rate=Decimal("0.025"))
    m = compute_metrics(
        equity_curve=eq, fills=[], initial_cash=Decimal("100000"), config=cfg
    )
    # 1 day of returns -> vol None -> sharpe None.
    assert m.sharpe_ratio is None


def test_sharpe_with_three_days_of_varying_returns():
    # 100 -> 110 -> 121 -> 110. Returns: 0, 0.10, 0.10, -0.0909.
    # daily_returns[1:] = [0.10, 0.10, -0.0909...]
    # stdev (sample) of those = 0.1087...
    eq = [
        EquityPoint(
            "20240102",
            Decimal("100000"),
            Decimal("0"),
            Decimal("100000"),
            Decimal("0"),
            Decimal("0"),
        ),
        EquityPoint(
            "20240103",
            Decimal("90000"),
            Decimal("0"),
            Decimal("110000"),
            Decimal("0.10"),
            Decimal("0"),
        ),
        EquityPoint(
            "20240104",
            Decimal("80000"),
            Decimal("0"),
            Decimal("121000"),
            Decimal("0.10"),
            Decimal("0"),
        ),
        EquityPoint(
            "20240105",
            Decimal("90000"),
            Decimal("0"),
            Decimal("110000"),
            Decimal("-0.0909"),
            Decimal("0.0909"),
        ),
    ]
    m = compute_metrics(
        equity_curve=eq,
        fills=[],
        initial_cash=Decimal("100000"),
        config=MetricsConfig(risk_free_rate=Decimal("0.0")),
    )
    assert m.daily_volatility is not None
    assert m.annualized_volatility is not None
    assert m.sharpe_ratio is not None
    # daily = per-day stdev; annualized = daily * sqrt(252).
    from math import sqrt

    assert m.annualized_volatility.quantize(Decimal("0.0001")) == (
        m.daily_volatility * Decimal(str(sqrt(252)))
    ).quantize(Decimal("0.0001"))
    # Annualised return ≈ 0.10 (3 trading days, very short; result is
    # approximate).
    # We just assert sharpe_ratio is finite (i.e. risk-free 0 + vol != 0).
    assert m.sharpe_ratio > 0  # excess return = total return > 0


# --------------------------------------------------------------------- #
# EquityPoint / PerformanceMetrics dataclass basics
# --------------------------------------------------------------------- #


def test_equity_point_is_frozen():
    pt = EquityPoint(
        "20240102",
        Decimal("100"),
        Decimal("0"),
        Decimal("100"),
        Decimal("0"),
        Decimal("0"),
    )
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        pt.date = "20240103"  # type: ignore[misc]


def test_performance_metrics_is_frozen():
    m = PerformanceMetrics(
        total_return=Decimal("0"),
        annualized_return=None,
        daily_volatility=None,
        annualized_volatility=None,
        sharpe_ratio=None,
        max_drawdown=Decimal("0"),
        turnover=Decimal("0"),
        trade_count=0,
        win_rate=None,
    )
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        m.trade_count = 1  # type: ignore[misc]
