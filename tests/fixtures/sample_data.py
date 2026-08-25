"""Shared test fixtures for the end-to-end example tests.

The data here is intentionally small, deterministic and hand-traceable.
The end-to-end tests in `tests/examples/` use these fixtures and verify
that running `examples/buy_and_hold.py` and `examples/moving_average.py`
against this data produces the hand-calculated outcomes.

Dates / prices:

    date        open   close
    20240102    10.00  10.00
    20240103    10.00  11.00
    20240104    10.00  10.00
    20240105    10.00  11.00
    20240108    10.00  12.00
    20240109    10.00  10.00
    20240110    10.00  12.00

Open is fixed at 10 so the broker always fills at the same price; close
drives the equity snapshot. The close series rises to 12, drops to 10,
then recovers to 12, so the moving-average signal (close vs 5-day mean)
produces a BUY, then a SELL, then a final BUY that is cancelled at the
end of the window. Buy-and-hold ends with a single block of 100 shares.
"""

from decimal import Decimal

from hqbacktest.data import InMemoryDataPortal
from hqbacktest.domain.bar import Bar


# 7 trading days; covers T+1 settlement (D+1 match) and the MA example's
# 5-day trailing window.
DATES: list[str] = [
    "20240102",
    "20240103",
    "20240104",
    "20240105",
    "20240108",
    "20240109",
    "20240110",
]

# (open, close) per date.
PRICES: dict[str, tuple[str, str]] = {
    "20240102": ("10.00", "10.00"),
    "20240103": ("10.00", "11.00"),
    "20240104": ("10.00", "10.00"),
    "20240105": ("10.00", "11.00"),
    "20240108": ("10.00", "12.00"),
    "20240109": ("10.00", "10.00"),
    "20240110": ("10.00", "12.00"),
}


def build_portal() -> InMemoryDataPortal:
    """Return a deterministic 7-day `InMemoryDataPortal` for example tests."""
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


# Buy-and-hold semantics: the example places a single BUY of 100 shares on
# the first BAR_CLOSE (20240102), matched at OPEN_MATCH(20240103) @ open=10.
# It then holds to the end of the run, so there is exactly one fill and no
# cancelled order.
#
# Cost of the BUY = 100 * 10 + 5 commission (0.025% = 0.25, floored at 5) = 1005.
# Final cash = 100000 - 1005 = 98995.
# Position: 100 shares @ close(20240110) = 12.00 -> market_value 1200.
# Total equity = 98995 + 1200 = 100195.
EXPECTED_BUY_HOLD_FINAL_CASH = Decimal("98995")
EXPECTED_BUY_HOLD_FILLED_TRADES = 1
EXPECTED_BUY_HOLD_FINAL_QUANTITY = 100
EXPECTED_BUY_HOLD_FINAL_EQUITY = EXPECTED_BUY_HOLD_FINAL_CASH + Decimal("1200")


# Moving-average semantics (5-day close mean vs latest close):
#   20240108  last 12.00 > mean 10.80  -> target 95% -> BUY 7900
#   20240109  last 10.00 <= mean 10.80 -> flatten    -> SELL 7900
#   20240110  last 12.00 > mean 11.00  -> target 95% -> BUY 7900 (cancelled)
#
# The 20240108 BUY fills at OPEN_MATCH(20240109) @ open=10:
#   7900 * 10 = 79000, commission max(79000*0.00025, 5) = 19.75.
# The 20240109 SELL fills at OPEN_MATCH(20240110) @ open=10 (T+1: shares
# bought on 20240109 become sellable after end-of-day settlement):
#   +79000 - 19.75 commission - 79.00 stamp tax.
# Final cash = 100000 - (79000 + 19.75) + (79000 - 19.75 - 79.00) = 99881.50.
EXPECTED_MA_FINAL_CASH = Decimal("99881.50")
EXPECTED_MA_FILLED_TRADES = 2
