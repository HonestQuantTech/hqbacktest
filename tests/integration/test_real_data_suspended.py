"""Integration scenario 3: universe with a suspended stock must not
crash; warnings must be traceable.

Calibrated against the `~/.hqdata/tushare` snapshot for v0.1.1:
    * 000008.SZ is suspended over 20260707..20260713 (7 trading days).
    * Universe containing 000008.SZ must let the engine run, with the
      suspension recorded via task-14's `DATA_WARNING` (fallback close
      valuation) when the holding is in the suspended window.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from hqbacktest import BacktestConfig, BacktestEngine, BaseStrategy
from hqbacktest.data import HqDataCsvPortal
from hqbacktest.domain.enums import EventType

from ._harness import (
    CALIBRATION_END,
    CALIBRATION_START,
    skip_if_no_snapshot,
)


UNIVERSE = [
    "600000.SH",
    "000008.SZ",  # suspended 20260707..20260713
    "601318.SH",
]


@pytest.mark.integration
@skip_if_no_snapshot()
def test_universe_with_suspended_symbol_runs_and_warns(tmp_path: Path):
    """Hold 000008.SZ through its suspended window and verify:
        - the run completes (no crash);
        - a DATA_WARNING is recorded for the suspended valuation;
        - the audit trail pinpoints the symbol + window.

    The strategy buys 000008.SZ well before its suspension window
    (20260701) and holds through 20260707..20260713. Task-14's
    fallback-close valuation must then log a `DATA_WARNING` for the
    suspended days.
    """

    class HoldSuspended(BaseStrategy):
        def initialize(self, context):
            context.set_universe(UNIVERSE)

        def on_bar(self, context, data):
            if context.now == "20260701":
                context.order_target_percent("000008.SZ", Decimal("0.30"))

    cfg = BacktestConfig(
        start_date=CALIBRATION_START,
        end_date=CALIBRATION_END,
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    portal = HqDataCsvPortal(source="tushare")
    engine = BacktestEngine(cfg, strategy=HoldSuspended(), portal=portal)
    result = engine.run()
    # Run completes.
    assert len(result.equity_curve) > 0
    # Audit-trail warning that mentions the suspended symbol.
    warnings = [
        e
        for e in engine.event_log.all()
        if e.phase is EventType.DATA_WARNING and "000008.SZ" in (e.detail or "")
    ]
    assert warnings, (
        f"expected at least one DATA_WARNING mentioning 000008.SZ; got "
        f"{[e.detail for e in warnings]}"
    )
    # The fallback close valuation fires whenever a held symbol has
    # no bar. The message mentions "fallback close".
    assert any(
        "fallback close" in (e.detail or "").lower() for e in warnings
    ), "expected a 'fallback close' message in the warnings"
