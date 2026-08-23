"""End-to-end regression test for `examples/moving_average.py` (task 11)."""

import importlib.util
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from hqbacktest import BacktestConfig, BacktestEngine

from tests.fixtures.sample_data import (
    DATES,
    EXPECTED_MA_FILLED_TRADES,
    EXPECTED_MA_FINAL_CASH,
    build_portal,
)


def _load_example_module():
    path = Path(__file__).resolve().parents[2] / "examples" / "moving_average.py"
    spec = importlib.util.spec_from_file_location("ma_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plain_mean(values):
    if not values:
        return Decimal("0")
    return sum(values) / Decimal(len(values))


def test_moving_average_runs_end_to_end():
    """Run the moving-average example; lock the hand-calculated round trip.

    The 5-day close mean vs latest close yields:
      20240108 -> BUY 7900 (95% of equity @ close 12.00)
      20240109 -> SELL 7900 (flatten; fills the next day, after T+1 settle)
      20240110 -> BUY 7900 (no next OPEN_MATCH -> cancelled at end of run)
    """
    module = _load_example_module()
    portal = build_portal()
    config = BacktestConfig(
        start_date=DATES[0],
        end_date=DATES[-1],
        initial_cash=Decimal("100000"),
        source="example",
    )
    engine = BacktestEngine(
        config, strategy=module.MovingAverageStrategy(), portal=portal
    )
    result = engine.run()

    # Two fills: BUY on 20240109, SELL on 20240110 (T+1: the shares bought
    # on 20240109 become sellable only after end-of-day settlement).
    fills = result.fills_table
    assert len(fills) == EXPECTED_MA_FILLED_TRADES
    assert [f["side"] for f in fills] == ["BUY", "SELL"]
    assert fills[0]["filled_at"] == "20240109"
    assert fills[1]["filled_at"] == "20240110"

    # Final state is fully flat and hand-calculated (see fixture comments).
    last = result.equity_curve[-1]
    assert last.cash == EXPECTED_MA_FINAL_CASH
    assert last.market_value == Decimal("0.00")
    assert last.total_equity == EXPECTED_MA_FINAL_CASH

    # The final BUY placed at BAR_CLOSE(20240110) has no next OPEN_MATCH and
    # is cancelled with BACKTEST_ENDED.
    statuses = [o["status"] for o in result.orders_table]
    assert statuses.count("FILLED") == 2
    assert statuses.count("CANCELLED") == 1

    assert result.metrics.trade_count == EXPECTED_MA_FILLED_TRADES


def test_moving_average_uses_public_api_only():
    """The strategy class body must not reach into engine internals.

    The `main()` wrapper legitimately calls `BacktestEngine(...).run()`
    (public API). The restriction is on the strategy class only.
    """
    path = Path(__file__).resolve().parents[2] / "examples" / "moving_average.py"
    text = path.read_text(encoding="utf-8")
    # Extract the full `MovingAverageStrategy` class body: from its header to
    # the next top-level statement (column 0). A naive split on the first
    # blank line would stop inside the class and silently skip `on_bar`.
    lines = text.splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("class MovingAverageStrategy")
    )
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][0].isspace()):
        end += 1
    strategy_body = "\n".join(lines[start:end])
    forbidden = (
        "_portfolio",
        "_event_log",
        "_order_counter",
        "engine.",
    )
    for token in forbidden:
        assert (
            token not in strategy_body
        ), f"strategy body uses internal symbol: {token!r}"


def test_moving_average_uses_only_public_mean_helper():
    """No numpy/pandas required: the example computes its own mean."""
    module = _load_example_module()
    closes = [Decimal("10"), Decimal("11"), Decimal("12"), Decimal("13"), Decimal("14")]
    assert module._mean(closes) == Decimal("12")
    assert module._mean([]) == Decimal("0")


def test_moving_average_example_script_runs():
    """`python examples/moving_average.py` exits 0 and prints a final cash line."""
    repo = Path(__file__).resolve().parents[2]
    script = repo / "examples" / "moving_average.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(repo),
        check=True,
    )
    assert "Final cash" in result.stdout
    assert "Total equity" in result.stdout


def test_moving_average_strategy_only_uses_public_context():
    """The strategy method body must only touch `context.*` public attrs."""
    module = _load_example_module()
    path = Path(__file__).resolve().parents[2] / "examples" / "moving_average.py"
    text = path.read_text(encoding="utf-8")
    # Inside the strategy class, only `context.*` and `data.*` are allowed.
    forbidden = (
        "engine._",
        "context._",
        "context.portfolio",
        "context.event_log",
    )
    for token in forbidden:
        assert token not in text, f"strategy touches internal: {token!r}"
