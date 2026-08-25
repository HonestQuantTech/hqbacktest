"""Integration scenario 2: 5-symbol moving-average strategy over the
full window, with a wall-clock budget and byte-determinism check.

Calibrated against the `~/.hqdata/tushare` snapshot for v0.1.1:
    * 5 picked symbols (well-known large caps).
    * Run budget: < 60 s for the full 139-day window.
    * Two runs with identical inputs must produce byte-identical output
      (excludes the `timestamp_utc` field in `run_metadata.json`,
      which is non-deterministic by design).

The strategy is defined inline (a local `strategy.py` written into
`tmp_path`) so the universe is configurable from the test instead
of being hard-coded inside `examples.moving_average`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from hqbacktest.cli.runner import run_from_file

from ._harness import (
    CALIBRATION_END,
    CALIBRATION_START,
    skip_if_no_snapshot,
)


UNIVERSE = [
    "600000.SH",
    "000001.SZ",
    "601318.SH",
    "600519.SH",
    "000333.SZ",
]


_STRATEGY_SRC = '''
from decimal import Decimal
from hqbacktest import BaseStrategy


class FiveSymbolMA(BaseStrategy):
    """5-symbol moving average, configurable universe via __init__."""

    def __init__(self, universe):
        super().__init__()
        self.universe = list(universe)

    def initialize(self, context):
        context.set_universe(self.universe)

    def on_bar(self, context, data):
        for sym in self.universe:
            closes = data.history(sym, field="close", bar_count=5)
            if len(closes) < 5:
                continue
            avg = sum(closes) / Decimal(len(closes))
            if closes[-1] > avg:
                context.order_target_percent(sym, Decimal("0.20"))
            else:
                context.order_target(sym, 0)
'''


@pytest.mark.integration
@skip_if_no_snapshot()
def test_5_symbol_moving_average_full_window_deterministic(tmp_path: Path):
    """Two consecutive runs of a 5-symbol MA strategy produce the
    same equity_curve.csv / summary.json / fills.csv bytes (modulo
    the timestamp field).

    Calibrated to < 60 s on the v0.1.1 release machine; the
    threshold is intentionally generous to survive CI jitter.
    """
    (tmp_path / "strategy.py").write_text(_STRATEGY_SRC)
    cfg_template = (
        "[start]\n"
        f"start_date = '{CALIBRATION_START}'\n"
        f"end_date = '{CALIBRATION_END}'\n"
        "[capital]\n"
        "initial_cash = '1000000'\n"
        "[data]\n"
        "source = 'tushare'\n"
        "[strategy]\n"
        "module = 'strategy'\n"
        f"kwargs = {{ universe = {json.dumps(UNIVERSE)} }}\n"
        "[output]\n"
        "directory = '__OUT__'\n"
    )

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    cfg_a = tmp_path / "c_a.toml"
    cfg_a.write_text(cfg_template.replace("__OUT__", str(out_a)))
    cfg_b = tmp_path / "c_b.toml"
    cfg_b.write_text(cfg_template.replace("__OUT__", str(out_b)))

    t0 = time.monotonic()
    result_a = run_from_file(str(cfg_a), force=True)
    elapsed = time.monotonic() - t0
    assert result_a.exit_code == 0, result_a.message
    assert elapsed < 60.0, f"5-symbol MA took {elapsed:.2f}s (>60s)"

    result_b = run_from_file(str(cfg_b), force=True)
    assert result_b.exit_code == 0, result_b.message

    # The deterministic comparison excludes `timestamp_utc` (wall
    # clock); every other output file must be byte-identical.
    for name in (
        "equity_curve.csv",
        "orders.csv",
        "fills.csv",
        "positions.csv",
        "costs.csv",
        "summary.json",
        "events.jsonl",
    ):
        a = (out_a / name).read_bytes()
        b = (out_b / name).read_bytes()
        assert a == b, f"{name} differs between runs"
    meta_a = json.loads((out_a / "run_metadata.json").read_text())
    meta_b = json.loads((out_b / "run_metadata.json").read_text())
    # `timestamp_utc` is wall-clock; `config_output_directory` and
    # `output_directory` reflect the per-run config path. Both are
    # expected to differ; everything else must match.
    nondeterministic = {
        "timestamp_utc",
        "config_output_directory",
        "output_directory",
        "config_path",
    }
    for k in meta_a:
        if k in nondeterministic:
            continue
        assert meta_a[k] == meta_b[k], f"run_metadata {k} differs"
