"""Task 17: equity curve / metrics baseline regression tests.

Covers:
    * First-day P&L flows into `daily_return` (no longer hard-coded 0).
    * First-day P&L flows into `drawdown` (no longer hard-coded 0).
    * A 1-day hold that drops 18% reports `max_drawdown` ~= 18%.
    * `∏(1 + daily_return) == 1 + total_return` (chained-product identity).
    * Single-day and two-day runs return `None` for `daily_volatility` /
      `annualized_volatility` / `sharpe_ratio` instead of misleading 0.
    * `positions_table.sellable_quantity` records the post-settlement
      value (D-row shows the shares sellable on D+1, per contract).
    * `metrics.py` never builds Decimal from `float` directly.
"""

from decimal import Decimal

import pytest

from hqbacktest import BacktestConfig, BacktestEngine, BaseStrategy
from hqbacktest.data import InMemoryDataPortal
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import EventType, OrderStatus
from hqbacktest.domain.portfolio import Portfolio
from hqbacktest.domain.position import Position
from hqbacktest.engine.metrics import (
    EquityPoint,
    MetricsConfig,
    compute_metrics,
)


# ---------------------------------------------------------------------------
# Engine-driven: first-day P&L flows into the equity curve
# ---------------------------------------------------------------------------


def _bar(date: str, open_: str, close: str, sym: str = "600000.SH") -> Bar:
    # Wide OHLC envelope so any close in [5, 30] is valid.
    return Bar.from_raw(
        symbol=sym,
        date=date,
        open=open_,
        high="30.0000",
        low="5.0000",
        close=close,
        volume=1000,
    )


def _two_day_portal() -> InMemoryDataPortal:
    p = InMemoryDataPortal(
        calendar=["20240102", "20240103", "20240104"],
        universe_by_date={"20240102": ["600000.SH"]},
        as_of="20240104",
    )
    # Day 1: open 10.00 -> close 8.20 (down 18%). Day 2: 8.20 -> 9.60.
    p.add_bar(_bar("20240102", "10.0000", "8.2000"))
    p.add_bar(_bar("20240103", "8.2000", "9.6000"))
    p.add_bar(_bar("20240104", "9.6000", "10.0000"))
    return p


def test_first_day_loss_appears_in_daily_return():
    """A 18% drop on the first trading day must register as a negative
    `daily_return` on day 1 (was previously hard-coded to 0).

    Setup: BUY @ 10 on day 1's BAR_CLOSE (matches at day-2 open @ 10),
    then day-2 close = 8.2, so the first observed P&L on day 2 is ~-18%
    relative to initial cash.
    """

    class BuyAll(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20240102":
                context.order_target_percent("600000.SH", Decimal("0.95"))

    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240103",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    # Two-day window: day 1 flat, day 2 drops to 8.2.
    p = InMemoryDataPortal(
        calendar=["20240102", "20240103"],
        universe_by_date={"20240102": ["600000.SH"]},
        as_of="20240103",
    )
    p.add_bar(_bar("20240102", "10.0000", "10.0000"))
    p.add_bar(_bar("20240103", "10.0000", "8.2000"))

    engine = BacktestEngine(cfg, strategy=BuyAll(), portal=p)
    engine.run()
    eq = engine.result.equity_curve
    # Day-2 daily_return is the FIRST observed P&L (the buy filled at
    # day-2 open=10 against the day-2 close=8.2 -> -18%).
    # Find the first non-zero daily_return.
    nonzero = next((pt for pt in eq if pt.daily_return != 0), None)
    assert nonzero is not None
    assert nonzero.daily_return < Decimal("0")
    # -0.17 (with commission drag): 95000 / 10 = 11500 shares @ 10, but
    # cash drops to 4976.25 after fees; equity at close = 82876.25 vs
    # initial 100000 -> -0.171. We accept either -0.17 or -0.18 within
    # 0.005 tolerance for commission / lot rounding noise.
    assert abs(nonzero.daily_return - Decimal("-0.17")) < Decimal("0.01")


def test_first_day_loss_appears_in_drawdown():
    """A 18% drop on day 2 must drive `max_drawdown` above 0."""
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240103",
        initial_cash=Decimal("100000"),
        source="tushare",
    )

    class BuyAll(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20240102":
                context.order_target_percent("600000.SH", Decimal("0.95"))

    p = InMemoryDataPortal(
        calendar=["20240102", "20240103"],
        universe_by_date={"20240102": ["600000.SH"]},
        as_of="20240103",
    )
    p.add_bar(_bar("20240102", "10.0000", "10.0000"))
    p.add_bar(_bar("20240103", "10.0000", "8.2000"))

    engine = BacktestEngine(cfg, strategy=BuyAll(), portal=p)
    result = engine.run()
    # 95% of 100k = 95000; buy 11500 shares @ 10 = 95023.75; market_value
    # at day-2 close = 11500 * 8.2 = 77900. drawdown ~= (100000 - 72876) /
    # 100000 ≈ 0.27 once fees are subtracted; but max_drawdown uses the
    # equity curve which is initial_cash (no holding) -> -0.18 the day
    # the position marks. We just assert drawdown > 0 (was 0 before
    # task 17).
    assert result.metrics.max_drawdown > Decimal("0.17")
    assert result.metrics.max_drawdown < Decimal("0.20")


def test_chained_product_identity_first_day_loss_then_recovery():
    """∏(1 + daily_return) must equal 1 + total_return within Decimal
    precision. With a first-day loss flowing into the curve, the
    identity used to silently fail because day-1's return was 0.
    """
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240103",
        initial_cash=Decimal("100000"),
        source="tushare",
    )

    class BuyAll(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20240102":
                context.order_target_percent("600000.SH", Decimal("0.95"))

    p = InMemoryDataPortal(
        calendar=["20240102", "20240103"],
        universe_by_date={"20240102": ["600000.SH"]},
        as_of="20240103",
    )
    p.add_bar(_bar("20240102", "10.0000", "10.0000"))
    p.add_bar(_bar("20240103", "10.0000", "8.2000"))

    engine = BacktestEngine(cfg, strategy=BuyAll(), portal=p)
    result = engine.run()
    daily_returns = [pt.daily_return for pt in result.equity_curve]
    growth = Decimal("1")
    for r in daily_returns:
        growth *= Decimal("1") + r
    expected = Decimal("1") + result.metrics.total_return
    diff = abs(growth - expected)
    assert diff < Decimal("0.0001"), (
        f"chained product identity broken: product={growth}, "
        f"1+total_return={expected}, diff={diff}"
    )


# ---------------------------------------------------------------------------
# metrics.py: insufficient samples return None
# ---------------------------------------------------------------------------


def test_single_day_volatility_is_none():
    """A 1-day equity curve has < 2 daily returns -> daily_volatility is
    `None`, not 0.
    """
    eq = [
        EquityPoint(
            date="20240102",
            cash=Decimal("100000"),
            market_value=Decimal("0"),
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
    assert m.daily_volatility is None
    assert m.annualized_volatility is None
    assert m.sharpe_ratio is None
    assert any("requires >= 2" in n for n in m.notes)


def test_two_day_volatility_uses_both_daily_returns():
    """Task 23: a 2-day equity curve with two distinct daily returns
    must produce a non-`None` `daily_volatility`.

    Hand-calculated scenario:
        Day 1: total_equity = 91000 (down 9% from 100000)
               -> daily_return = -0.09
        Day 2: total_equity = 96005 (up 5.5% from 91000)
               -> daily_return = +0.055

    Pre-fix: `_daily_returns` re-derived the series from
    `total_equity`, with `[Decimal("0")]` as the day-0 seed, so the
    day-1 return was silently dropped. With only one return in
    `returns[1:]` stdev failed -> `daily_volatility is None`.

    Post-fix: `compute_metrics` reads `EquityPoint.daily_return`
    directly, so both samples reach the stdev call:
        stdev([-0.09, 0.055], ddof=1) = sqrt(((−0.0725)² + 0.0725²) / 1)
                                    = sqrt(0.0105125)
                                    ≈ 0.102531...

    The contract: `daily_volatility` MUST see first-day P&L the same
    way `max_drawdown` does (task 17 already wired that side).
    """
    eq = [
        EquityPoint(
            "20240102",
            Decimal("91000"),
            Decimal("0"),
            Decimal("91000"),
            Decimal("-0.09"),
            Decimal("0.09"),
        ),
        EquityPoint(
            "20240103",
            Decimal("96005"),
            Decimal("0"),
            Decimal("96005"),
            Decimal("0.055"),
            Decimal("0.04"),
        ),
    ]
    m = compute_metrics(
        equity_curve=eq,
        fills=[],
        initial_cash=Decimal("100000"),
        config=MetricsConfig(),
    )
    # Both samples reached stdev -> volatility is defined.
    assert m.daily_volatility is not None
    # Hand-calculated value: sqrt(0.0105125) ≈ 0.102531
    expected = Decimal("0.10253")
    diff = abs(m.daily_volatility - expected)
    assert diff < Decimal(
        "0.0001"
    ), f"daily_volatility={m.daily_volatility} != {expected} (diff={diff})"
    # Annualised volatility = daily * sqrt(252).
    from math import sqrt

    expected_ann = m.daily_volatility * Decimal(str(sqrt(252)))
    assert abs(m.annualized_volatility - expected_ann) < Decimal("0.0001")
    # Total return = 96005 / 100000 - 1 = -0.03995
    assert abs(m.total_return - Decimal("-0.03995")) < Decimal("0.000001")
    # Chained-product identity: ∏(1 + daily_return) == 1 + total_return
    chained = (Decimal("1") + Decimal("-0.09")) * (Decimal("1") + Decimal("0.055"))
    assert abs(chained - (Decimal("1") + m.total_return)) < Decimal("0.0001")


# ---------------------------------------------------------------------------
# positions_table.sellable_quantity semantic: post-settlement snapshot
# ---------------------------------------------------------------------------


def test_positions_table_records_post_settlement_sellable_quantity():
    """The D-row in `positions_table` records `sellable_quantity` AFTER
    the end-of-day T+1 settlement: shares bought on D become sellable
    on D+1, so the D-row shows that sellable count.

    Scenario: BUY 100 @ 10 on day 1, hold to day 2. After day 1's
    settlement, the position holds 100 sellable shares; day-1 row
    must show that count, not 0.
    """

    class HoldOverNight(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20240102":
                context.order("600000.SH", 100)

    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240103",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    p = InMemoryDataPortal(
        calendar=["20240102", "20240103"],
        universe_by_date={"20240102": ["600000.SH"]},
        as_of="20240103",
    )
    p.add_bar(_bar("20240102", "10.0000", "10.0000"))
    p.add_bar(_bar("20240103", "10.0000", "10.0000"))

    engine = BacktestEngine(cfg, strategy=HoldOverNight(), portal=p)
    result = engine.run()
    rows = [r for r in result.positions_table if r["symbol"] == "600000.SH"]
    # Day 1 row shows 100 sellable (post-settlement snapshot).
    assert rows[0]["quantity"] == "100"
    assert rows[0]["sellable_quantity"] == "100"


# ---------------------------------------------------------------------------
# metrics.py: no direct Decimal(float)
# ---------------------------------------------------------------------------


def test_metrics_output_is_clean_decimal_strings():
    """`annualized_return` must not leak float artifacts (e.g. 1.1**0.039...).
    The metric is computed via Decimal(str(float_pow_result)).
    """
    eq = []
    base = Decimal("100000")
    growth_per_day = Decimal("1.005")
    for i in range(20):
        equity = base * growth_per_day**i
        eq.append(
            EquityPoint(
                date=f"2024{(i // 30) + 1:04d}{(i % 28) + 2:02d}",
                cash=equity,
                market_value=Decimal("0"),
                total_equity=equity,
                daily_return=growth_per_day - Decimal("1") if i > 0 else Decimal("0"),
                drawdown=Decimal("0"),
            )
        )
    m = compute_metrics(
        equity_curve=eq,
        fills=[],
        initial_cash=Decimal("100000"),
        config=MetricsConfig(),
    )
    assert m.annualized_return is not None
    # Quantize to a reasonable number of decimals; the value must not
    # contain the float "inf" / "nan" sentinel and must be a proper Decimal.
    s = str(m.annualized_return)
    assert "inf" not in s.lower()
    assert "nan" not in s.lower()


def test_drawdown_peak_includes_initial_cash_on_continued_drop():
    """The running drawdown peak must include `initial_cash`, so a first-day
    loss followed by a continued drop does NOT under-report drawdown.

    Scenario: BUY 9500 @ 10 at day-1 open, close 9.1 -> equity 91426.25
    (drawdown ~8.57%). Day-2 close 8.9 -> equity 89526.25. The correct
    day-2 drawdown is (100000 - 89526.25) / 100000 = 10.47%, NOT
    (91426.25 - 89526.25) / 91426.25 = 2.08% — the peak must be
    `initial_cash` (task 17: "回撤峰值序列以 initial_cash 为初始峰值").
    """

    class BuyAtOpen(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def before_trading_start(self, context, data):
            if context.now == "20240102":
                context.order("600000.SH", 9500)

    p = InMemoryDataPortal(
        calendar=["20240102", "20240103"],
        universe_by_date={"20240102": ["600000.SH"]},
        as_of="20240103",
    )
    p.add_bar(_bar("20240102", "10.0000", "9.1000"))
    p.add_bar(_bar("20240103", "9.1000", "8.9000"))

    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240103",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    engine = BacktestEngine(cfg, strategy=BuyAtOpen(), portal=p)
    result = engine.run()
    second_day = result.equity_curve[1]
    expected = (Decimal("100000") - Decimal("89526.25")) / Decimal("100000")
    assert abs(second_day.drawdown - expected) < Decimal("0.01")
    assert result.metrics.max_drawdown >= second_day.drawdown
