"""Integration scenario 4: first-trading-day `before_trading_start`
reading `current_price` against the real snapshot.

Per task 14, the first trading day uses the sentinel `visible_through`
of `"00000000"`, so `current_price` must return `None` rather than
crashing. This guards the documented sentinel contract end-to-end
against a real CSV portal (where the empty/invalid date would
otherwise trip the validator).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from hqbacktest import BacktestConfig, BacktestEngine, BaseStrategy
from hqbacktest.data import HqDataCsvPortal

from ._harness import (
    CALIBRATION_END,
    CALIBRATION_START,
    skip_if_no_snapshot,
)


class ReadPriceFirstDay(BaseStrategy):
    """Read `current_price` on the first trading day's
    `before_trading_start`. Must return `None`, not raise.
    """

    seen: list = []

    def initialize(self, context):
        context.set_universe(["600000.SH"])

    def before_trading_start(self, context, data):
        if context.now == CALIBRATION_START:
            price = context.current_price("600000.SH")
            self.seen.append(price)


@pytest.mark.integration
@skip_if_no_snapshot()
def test_before_trading_start_current_price_first_day():
    """First-day `current_price` returns None (sentinel visible_through)
    without raising against the real CSV portal.
    """
    strategy = ReadPriceFirstDay()
    cfg = BacktestConfig(
        start_date=CALIBRATION_START,
        end_date=CALIBRATION_END,
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    portal = HqDataCsvPortal(source="tushare")
    engine = BacktestEngine(cfg, strategy=strategy, portal=portal)
    engine.run()  # must not raise
    # Strategy ran at least once on the first day.
    assert len(strategy.seen) == 1
    # The sentinel view returned None, not 0 or a real price.
    assert strategy.seen[0] is None
