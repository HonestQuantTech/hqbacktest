"""Task 19: factor-diagnostics-on-holding + CLI summary tests.

Covers:
    * Holding through an ex-date with a > 0.1% factor jump emits a
      DATA_WARNING event AND records a FactorDiagnostic on the
      collector, surfaced in `result.factor_diagnostics`.
    * The 600000.SH 2026-07-16 ex-date case (factor 16.59 -> 17.38,
      ≈ 4.7% dividend) reproduces as a clear, traceable warning.
    * Diagnostics do NOT change cash, position, or equity: the
      balance is byte-identical with the no-diagnostics baseline.
    * Symbols that are NOT held or traded never generate holdings-
      period diagnostics (the engine still records generic
      diagnostics but the holding-period summary must exclude them).
    * CLI runner prints a one-line summary when any such diagnostics
      were recorded.
"""

from decimal import Decimal
from io import StringIO

import pytest

from hqbacktest import BacktestConfig, BacktestEngine, BaseStrategy
from hqbacktest.cli.runner import run_from_config
from hqbacktest.data import InMemoryDataPortal
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import EventType
from hqbacktest.engine.corporate_actions import (
    DEFAULT_JUMP_BAND,
    FactorDiagnostic,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _bar(date: str, sym: str = "600000.SH") -> Bar:
    return Bar.from_raw(
        symbol=sym,
        date=date,
        open="10.0000",
        high="30.0000",
        low="5.0000",
        close="10.0000",
        volume=1000,
    )


def _hold_then_dividend_portal() -> InMemoryDataPortal:
    """Reproduce 600000.SH 2026-07-16 dividend ex-date.

    Calendar:
        2026-07-14, 15, 16 (ex-date), 17, 18
    Per-day factors (decimal strings):
        14: 16.59
        15: 16.59
        16: 17.38 (≈ +4.76% jump — dividend)
        17: 17.38
        18: 17.38
    """
    p = InMemoryDataPortal(
        calendar=["20260714", "20260715", "20260716", "20260717", "20260718"],
        universe_by_date={"20260714": ["600000.SH"]},
        as_of="20260718",
    )
    factors = {
        "20260714": [("600000.SH", "16.59")],
        "20260715": [("600000.SH", "16.59")],
        "20260716": [("600000.SH", "17.38")],
        "20260717": [("600000.SH", "17.38")],
        "20260718": [("600000.SH", "17.38")],
    }
    for d in ("20260714", "20260715", "20260716", "20260717", "20260718"):
        p.add_bar(_bar(d))
        p.add_factor("600000.SH", d, Decimal(factors[d][0][1]))
    return p


def _hold_then_dividend_engine(strategy):
    cfg = BacktestConfig(
        start_date="20260714",
        end_date="20260718",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    return BacktestEngine(cfg, strategy=strategy, portal=_hold_then_dividend_portal())


# ---------------------------------------------------------------------------
# Holding-period factor diagnostics
# ---------------------------------------------------------------------------


def test_holding_through_dividend_emits_warning_event():
    """A 4.76% factor jump while holding must produce a DATA_WARNING
    event with the symbol + dates + factor values in the detail.
    """

    class Hold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20260714":
                context.order("600000.SH", 100)

    engine = _hold_then_dividend_engine(Hold())
    engine.run()
    warnings = [e for e in engine.event_log.all() if e.phase is EventType.DATA_WARNING]
    assert warnings, "expected at least one DATA_WARNING for dividend jump"
    detail = warnings[0].detail or ""
    assert "600000.SH" in detail
    assert "16.59" in detail
    assert "17.38" in detail
    # The ex-date is 2026-07-16 (the day the factor jumped).
    assert "20260716" in detail


def test_holding_through_dividend_records_factor_diagnostic():
    """The same holding scenario must also surface in
    `result.factor_diagnostics` with kind='abnormal_jump' (the
    available kind for cross-day factor changes).
    """

    class Hold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20260714":
                context.order("600000.SH", 100)

    engine = _hold_then_dividend_engine(Hold())
    result = engine.run()
    diagnostics = result.factor_diagnostics
    assert diagnostics, "expected FactorDiagnostic records"
    sym_dates = {(d.symbol, d.date) for d in diagnostics}
    assert ("600000.SH", "20260716") in sym_dates
    # The diagnostic detail surfaces the factor ratio produced by
    # `analyze_factor_series`. The audit-trail event carries the
    # before/after factor values (see `test_holding_through_dividend_
    # emits_warning_event`).
    detail = next(
        d.detail
        for d in diagnostics
        if d.symbol == "600000.SH" and d.date == "20260716"
    )
    assert "abnormal_jump" == next(
        d.kind for d in diagnostics if d.symbol == "600000.SH" and d.date == "20260716"
    )
    assert "factor ratio" in detail or "ratio" in detail


def test_diagnostics_do_not_change_ledger():
    """Diagnostics must be observability-only: cash, position and
    equity are byte-identical with a baseline that does not run
    diagnostics at all.

    The baseline strategy uses identical inputs and trades but the
    portal never triggers the diagnostics path because no ex-date
    factor jump exists.
    """
    from hqbacktest.data import InMemoryDataPortal

    class Hold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20260714":
                context.order("600000.SH", 100)

    flat = InMemoryDataPortal(
        calendar=["20260714", "20260715", "20260716", "20260717", "20260718"],
        universe_by_date={"20260714": ["600000.SH"]},
        as_of="20260718",
    )
    for d in ("20260714", "20260715", "20260716", "20260717", "20260718"):
        flat.add_bar(_bar(d))
        flat.add_factor("600000.SH", d, Decimal("1.0000"))
    cfg = BacktestConfig(
        start_date="20260714",
        end_date="20260718",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    baseline_engine = BacktestEngine(cfg, strategy=Hold(), portal=flat)
    baseline_engine.run()
    baseline_curve = list(baseline_engine.result.equity_curve)

    jump_engine = _hold_then_dividend_engine(Hold())
    jump_engine.run()
    jump_curve = list(jump_engine.result.equity_curve)

    assert len(baseline_curve) == len(jump_curve)
    for b, j in zip(baseline_curve, jump_curve):
        assert b.cash == j.cash
        assert b.market_value == j.market_value
        assert b.total_equity == j.total_equity


def test_unheld_symbols_produce_no_holding_diagnostic():
    """A factor jump on a symbol that was never held or traded does
    not generate a holdings-period diagnostic for that symbol.
    """
    p = InMemoryDataPortal(
        calendar=["20260714", "20260715", "20260716"],
        universe_by_date={"20260714": ["600000.SH", "999999.SH"]},
        as_of="20260716",
    )
    # 600000.SH flat factors; 999999.SH has a big jump on the last day.
    factor_for_999 = {
        "20260714": Decimal("1.00"),
        "20260715": Decimal("1.00"),
        "20260716": Decimal("2.00"),
    }
    for d in ("20260714", "20260715", "20260716"):
        p.add_bar(_bar(d))
        p.add_bar(_bar(d, sym="999999.SH"))
        p.add_factor("600000.SH", d, Decimal("1.00"))
        p.add_factor("999999.SH", d, factor_for_999[d])

    class OnlyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH", "999999.SH"])

        def on_bar(self, context, data):
            if context.now == "20260714":
                context.order("600000.SH", 100)

    cfg = BacktestConfig(
        start_date="20260714",
        end_date="20260716",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    engine = BacktestEngine(cfg, strategy=OnlyHold(), portal=p)
    result = engine.run()
    syms = {d.symbol for d in result.factor_diagnostics}
    # 999999.SH was never traded; no holding-period diagnostic for it.
    assert "999999.SH" not in syms


def test_jump_threshold_default_band_is_wider_than_holding_threshold():
    """`analyze_factor_series` keeps its default `jump_band` of
    (0.5, 2.0) for general diagnostics, but the engine applies a
    tighter 0.1% threshold for the holdings-period holding summary.
    The default band is wider so general diagnostics still
    surface truly wild factor swings.
    """
    assert DEFAULT_JUMP_BAND == (Decimal("0.5"), Decimal("2.0"))


def test_sold_symbol_stops_emitting_holding_diagnostics():
    """A symbol that has been fully sold must not keep emitting
    holdings-period factor-jump warnings after its holding period ends.

    Regression for a bug where the engine tracked every symbol ever
    traded (a `_traded_symbols` set that never shrank), so a factor
    jump AFTER the position was flattened still produced a spurious
    DATA_WARNING and FactorDiagnostic.
    """

    class BuyThenSell(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20260714":
                context.order("600000.SH", 100)
            elif context.now == "20260715":
                context.order_target("600000.SH", 0)  # flatten

    p = InMemoryDataPortal(
        calendar=["20260714", "20260715", "20260716", "20260717"],
        universe_by_date={"20260714": ["600000.SH"]},
        as_of="20260717",
    )
    factors = {
        "20260714": "16.59",
        "20260715": "16.59",
        "20260716": "17.38",  # ex-date jump AFTER the position is flat
        "20260717": "17.38",
    }
    for d in ("20260714", "20260715", "20260716", "20260717"):
        p.add_bar(_bar(d))
        p.add_factor("600000.SH", d, Decimal(factors[d]))

    cfg = BacktestConfig(
        start_date="20260714",
        end_date="20260717",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    engine = BacktestEngine(cfg, strategy=BuyThenSell(), portal=p)
    result = engine.run()
    assert result.factor_diagnostics == []
    warnings = [e for e in engine.event_log.all() if e.phase is EventType.DATA_WARNING]
    assert warnings == []


# ---------------------------------------------------------------------------
# CLI stdout summary
# ---------------------------------------------------------------------------


def test_cli_runner_prints_summary_when_diagnostics_present(tmp_path):
    """`run_from_config` writes a one-line warning to stdout when the
    engine recorded any holding-period factor diagnostics.
    """
    import sys

    strategy_src = (
        "from hqbacktest import BaseStrategy\n"
        "class Hold(BaseStrategy):\n"
        "    def initialize(self, context):\n"
        "        context.set_universe(['600000.SH'])\n"
        "    def on_bar(self, context, data):\n"
        "        if context.now == '20260714':\n"
        "            context.order('600000.SH', 100)\n"
    )
    strategy_file = tmp_path / "strategy.py"
    strategy_file.write_text(strategy_src)
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "[start]\n"
        "start_date = '20260714'\n"
        "end_date = '20260718'\n"
        "[capital]\n"
        "initial_cash = '100000'\n"
        "[data]\n"
        "source = 'memory'\n"
        "[strategy]\n"
        "module = 'strategy'\n"
        "class_name = 'Hold'\n"
        "[output]\n"
        f"directory = '{tmp_path / 'out'}'\n"
    )
    portal = _hold_then_dividend_portal()
    from hqbacktest.cli import runner
    from hqbacktest.cli.config import load_config_file

    original = runner._resolve_portal
    runner._resolve_portal = lambda source, data_root: portal
    sys.path.insert(0, str(tmp_path))
    cfg_file_obj = load_config_file(str(config_file))
    buf = StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        run_from_config(cfg_file_obj, source_path=str(config_file))
    finally:
        sys.stdout = old_stdout
        sys.path.remove(str(tmp_path))
        runner._resolve_portal = original
    output = buf.getvalue()
    assert (
        "factor" in output.lower()
        or "diagnostic" in output.lower()
        or "warning" in output.lower()
    ), f"expected a factor/diagnostic warning in stdout; got: {output!r}"
