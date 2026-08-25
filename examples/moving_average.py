"""Moving-average example.

Run from the project root with::

    python examples/moving_average.py

The strategy uses the public `Context.history` API to look at the last
5 closes and compares the current close with their average: above the
average it targets 95% of equity, otherwise it flattens to 0. On the
7-day fixture this triggers a BUY, then a SELL (exercising the T+1
sellable cadence), then a final BUY that has no next OPEN_MATCH and is
cancelled at the end of the run.

End-to-end test (`tests/examples/test_moving_average.py`) re-runs the
same code path and locks in the hand-calculated final cash / position.
"""

from decimal import Decimal
import sys
from pathlib import Path
from typing import List

# Make `tests.fixtures` importable when this file is run as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hqbacktest import (
    BacktestConfig,
    BacktestEngine,
    BaseStrategy,
    InMemoryDataPortal,
)
from hqbacktest.domain.bar import Bar

from tests.fixtures.sample_data import DATES, PRICES


def _mean(values: List[Decimal]) -> Decimal:
    """Plain-Python mean (no numpy / pandas required)."""
    if not values:
        return Decimal("0")
    return sum(values) / Decimal(len(values))


class MovingAverageStrategy(BaseStrategy):
    """Compare the current close with its 5-day trailing average.

    Uses only the public `data.history` / `context.order_target*` API.
    Close > average -> target 95% of equity; otherwise flatten to 0.
    """

    def initialize(self, context) -> None:
        context.set_universe(["600000.SH"])

    def on_bar(self, context, data) -> None:
        closes = data.history("600000.SH", field="close", bar_count=5)
        if len(closes) < 5:
            return
        avg = _mean(closes)
        last = closes[-1]
        if last > avg:
            context.order_target_percent("600000.SH", Decimal("0.95"))
        else:
            context.order_target("600000.SH", 0)


def _build_portal_inline() -> InMemoryDataPortal:
    """Standalone portal builder."""
    p = InMemoryDataPortal(calendar=list(DATES), source="example", as_of="20240110")
    for d in DATES:
        op, cl = PRICES[d]
        p.add_bar(
            Bar.from_raw(
                symbol="600000.SH",
                date=d,
                open=op,
                high="15.00",
                low="9.00",
                close=cl,
                volume=1000,
            )
        )
    return p


def main() -> None:
    portal = _build_portal_inline()
    config = BacktestConfig(
        start_date=DATES[0],
        end_date=DATES[-1],
        initial_cash=Decimal("100000"),
        source="example",
    )
    engine = BacktestEngine(config, strategy=MovingAverageStrategy(), portal=portal)
    result = engine.run()

    last = result.equity_curve[-1]
    print(f"Trading days : {len(result.trading_days)}")
    print(f"Final cash   : {last.cash}")
    print(f"Market value : {last.market_value}")
    print(f"Total equity : {last.total_equity}")
    print(f"Total return : {result.metrics.total_return}")
    print(f"Trade count  : {result.metrics.trade_count}")


if __name__ == "__main__":
    main()
