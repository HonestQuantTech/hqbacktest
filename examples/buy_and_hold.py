"""Buy-and-hold example.

Run from the project root with::

    python examples/buy_and_hold.py

The example builds a deterministic 7-day `InMemoryDataPortal`, instantiates
a `BuyAndHold` strategy using the public `BaseStrategy` + `Context` API, runs
the backtest, and prints the resulting equity curve + metrics. No CSV files,
no network, no credentials.

End-to-end test (`tests/examples/test_buy_and_hold.py`) re-runs the same
code path and locks in the hand-calculated final cash / position / equity.
"""

from decimal import Decimal
import sys
from pathlib import Path

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


class BuyAndHold(BaseStrategy):
    """Buy 100 shares on the first BAR_CLOSE and hold to end of run.

    Uses only the public `Context` API: `set_universe` and `order`. Never
    touches the engine or portfolio.
    """

    def initialize(self, context) -> None:
        context.set_universe(["600000.SH"])
        self._entered = False

    def on_bar(self, context, data) -> None:
        # Buy exactly once, on the first BAR_CLOSE. The order is matched at
        # the next day's OPEN_MATCH; from then on we simply hold.
        if not self._entered:
            context.order("600000.SH", 100)
            self._entered = True


def _build_portal_inline() -> InMemoryDataPortal:
    """Standalone portal builder (does not import the test fixture)."""
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
    engine = BacktestEngine(config, strategy=BuyAndHold(), portal=portal)
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
