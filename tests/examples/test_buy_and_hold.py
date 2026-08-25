"""End-to-end regression test for `examples/buy_and_hold.py`.

Drives the public API: builds the same `InMemoryDataPortal` fixture the
example uses, instantiates `BuyAndHold` from the example module, and locks
the hand-calculated final state of the backtest. If the public API ever
breaks, this test fires.
"""

import importlib.util
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from hqbacktest import (
    BacktestConfig,
    BacktestEngine,
)

from tests.fixtures.sample_data import (
    DATES,
    EXPECTED_BUY_HOLD_FINAL_CASH,
    EXPECTED_BUY_HOLD_FINAL_EQUITY,
    EXPECTED_BUY_HOLD_FINAL_QUANTITY,
    EXPECTED_BUY_HOLD_FILLED_TRADES,
    build_portal,
)


def _load_example_module():
    """Import `examples/buy_and_hold.py` as a module."""
    path = Path(__file__).resolve().parents[2] / "examples" / "buy_and_hold.py"
    spec = importlib.util.spec_from_file_location("buy_and_hold_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------- #
# Direct in-process run
# --------------------------------------------------------------------- #


def test_buy_and_hold_runs_end_to_end_with_default_portal():
    """Run BuyAndHold on the shared 7-day fixture; verify final state."""
    module = _load_example_module()
    portal = build_portal()
    config = BacktestConfig(
        start_date=DATES[0],
        end_date=DATES[-1],
        initial_cash=Decimal("100000"),
        source="example",
    )
    engine = BacktestEngine(config, strategy=module.BuyAndHold(), portal=portal)
    result = engine.run()

    # Hand-calculated: 1 BUY * 1005 (1000 + 5 commission) = 1005.
    # End cash = 100000 - 1005 = 98995.
    # Position: 100 shares @ close(20240110) = 12.00 -> market_value 1200.
    # Total equity = 98995 + 1200 = 100195.
    assert result.equity_curve[-1].cash == EXPECTED_BUY_HOLD_FINAL_CASH
    assert result.equity_curve[-1].market_value == Decimal("1200.00")
    assert result.equity_curve[-1].total_equity == (EXPECTED_BUY_HOLD_FINAL_EQUITY)
    assert result.equity_curve[-1].market_value == Decimal(
        str(EXPECTED_BUY_HOLD_FINAL_QUANTITY)
    ) * Decimal("12.00")


def test_buy_and_hold_records_single_fill_no_cancelled():
    """One BUY matches the next day; nothing is left to cancel at the end."""
    module = _load_example_module()
    portal = build_portal()
    config = BacktestConfig(
        start_date=DATES[0],
        end_date=DATES[-1],
        initial_cash=Decimal("100000"),
        source="example",
    )
    engine = BacktestEngine(config, strategy=module.BuyAndHold(), portal=portal)
    result = engine.run()

    fills = result.fills_table
    assert len(fills) == EXPECTED_BUY_HOLD_FILLED_TRADES
    assert all(f["side"] == "BUY" for f in fills)
    # The only BUY is placed at BAR_CLOSE(20240102) -> OPEN_MATCH(20240103).
    assert fills[0]["filled_at"] == "20240103"
    # Order table has a single FILLED order and nothing cancelled.
    statuses = [o["status"] for o in result.orders_table]
    assert statuses == ["FILLED"]


def test_buy_and_hold_total_return_matches_hand_calculation():
    """Total return = (final_equity - initial_cash) / initial_cash."""
    module = _load_example_module()
    portal = build_portal()
    config = BacktestConfig(
        start_date=DATES[0],
        end_date=DATES[-1],
        initial_cash=Decimal("100000"),
        source="example",
    )
    engine = BacktestEngine(config, strategy=module.BuyAndHold(), portal=portal)
    result = engine.run()
    expected_total_return = (
        EXPECTED_BUY_HOLD_FINAL_EQUITY - Decimal("100000")
    ) / Decimal("100000")
    assert result.metrics.total_return.quantize(Decimal("0.0001")) == (
        expected_total_return.quantize(Decimal("0.0001"))
    )


# --------------------------------------------------------------------- #
# Subprocess: run the example exactly as a user would
# --------------------------------------------------------------------- #


def test_buy_and_hold_example_script_runs():
    """`python examples/buy_and_hold.py` exits 0 and prints a final cash line."""
    repo = Path(__file__).resolve().parents[2]
    script = repo / "examples" / "buy_and_hold.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(repo),
        check=True,
    )
    assert "Final cash" in result.stdout
    # The hand-calculated final cash must appear in stdout (rounded to 2dp).
    assert "98995" in result.stdout


def test_buy_and_hold_save_load_round_trip():
    """Save + load the result; equity curve + fills round-trip."""
    import tempfile

    module = _load_example_module()
    portal = build_portal()
    config = BacktestConfig(
        start_date=DATES[0],
        end_date=DATES[-1],
        initial_cash=Decimal("100000"),
        source="example",
    )
    engine = BacktestEngine(config, strategy=module.BuyAndHold(), portal=portal)
    result = engine.run()

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "result"
        result.save(str(out))
        loaded = type(result).load(str(out))
        assert [pt.date for pt in loaded.equity_curve] == [
            pt.date for pt in result.equity_curve
        ]
        assert loaded.fills_table[0]["order_id"] == result.fills_table[0]["order_id"]
