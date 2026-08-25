"""Integration scenario 1: buy_and_hold across the 2026-07-16 dividend.

Calibrated against the `~/.hqdata/tushare` snapshot:
    * 600000.SH factor jumps 16.5935 -> 17.3774 (~4.7%) on 20260716.
    * Buy-and-hold from 20260105 to 20260731 with 95% sizing must
      produce at least one DATA_WARNING factor diagnostic.
"""

from __future__ import annotations

import pytest

from hqbacktest import BacktestConfig, BacktestEngine, BaseStrategy
from hqbacktest.data import HqDataCsvPortal
from hqbacktest.domain.enums import EventType

from ._harness import (
    CALIBRATION_END,
    CALIBRATION_EX_DATE,
    CALIBRATION_START,
    skip_if_no_snapshot,
)


@pytest.mark.integration
@skip_if_no_snapshot()
def test_buy_and_hold_across_dividend_ex_date():
    """A 95%-of-cash buy on day 1 held through the 20260716 ex-date
    must produce at least one DATA_WARNING factor diagnostic.
    """
    from decimal import Decimal

    class Hold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == CALIBRATION_START:
                context.order_target_percent("600000.SH", Decimal("0.95"))

    cfg = BacktestConfig(
        start_date=CALIBRATION_START,
        end_date=CALIBRATION_END,
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    portal = HqDataCsvPortal(source="tushare")
    engine = BacktestEngine(cfg, strategy=Hold(), portal=portal)
    result = engine.run()
    # Factor-diagnostics collector must contain the 20260716 jump.
    diag_dates = {d.date for d in result.factor_diagnostics if d.symbol == "600000.SH"}
    assert (
        CALIBRATION_EX_DATE in diag_dates
    ), f"expected a 20260716 factor jump for 600000.SH; got {diag_dates}"
    # And the warning must appear in the event log.
    warnings = [
        e
        for e in engine.event_log.all()
        if e.phase is EventType.DATA_WARNING and "600000.SH" in (e.detail or "")
    ]
    assert any(
        CALIBRATION_EX_DATE in (e.detail or "") for e in warnings
    ), f"no DATA_WARNING mentions {CALIBRATION_EX_DATE}: {warnings}"
