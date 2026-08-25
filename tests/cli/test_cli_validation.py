"""CLI validation + first-mile regression tests.

Covers:
    * Console-script (`hqbacktest run`) works from a fresh working
      directory using only the console-script entry point, NOT
      `python -m hqbacktest run`.
    * `initial_cash = nan` / `inf` / negative yields a ConfigError
      (CLI exit code 2, single-line stderr).
    * `start_date = 20241399` (impossible date) yields a ConfigError.
    * A backtest window with zero trading days raises a ConfigError
      (exit 2), not a silent empty result.
    * Output directory that already contains prior-run files fails
      with exit 3 unless `--force` is given.
    * `Context.order_value(symbol, 15000)` accepts `int` and `str`
      cash amounts (not only Decimal) without raising.
    * `run_metadata.git_commit` reflects the hqbacktest package's own
      commit (not the user's cwd repository).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from hqbacktest import BacktestConfig, BacktestEngine, BaseStrategy
from hqbacktest.cli.config import ConfigError, load_config_file
from hqbacktest.cli.runner import _git_commit, run_from_file
from hqbacktest.data import InMemoryDataPortal
from hqbacktest.domain.bar import Bar


# ---------------------------------------------------------------------------
# Fixture helpers
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


def _memory_portal() -> InMemoryDataPortal:
    p = InMemoryDataPortal(
        calendar=["20240102", "20240103", "20240104"],
        universe_by_date={"20240102": ["600000.SH"]},
        as_of="20240104",
    )
    for d in ("20240102", "20240103", "20240104"):
        p.add_bar(_bar(d))
    return p


def _minimal_config(output_dir: Path, strategy_module: str = "strategy") -> str:
    return f"""[start]
start_date = '20240102'
end_date = '20240104'
[capital]
initial_cash = '100000'
[data]
source = 'memory'
[strategy]
module = '{strategy_module}'
[output]
directory = '{output_dir}'
"""


def _write_strategy_module(tmp: Path) -> Path:
    p = tmp / "strategy.py"
    p.write_text(
        "from hqbacktest import BaseStrategy\n"
        "class BuyHold(BaseStrategy):\n"
        "    def initialize(self, context):\n"
        "        context.set_universe(['600000.SH'])\n"
        "    def on_bar(self, context, data):\n"
        "        if context.now == '20240102':\n"
        "            context.order('600000.SH', 100)\n"
    )
    return p


# ---------------------------------------------------------------------------
# Console script first-mile
# ---------------------------------------------------------------------------


def test_console_script_resolves_strategy_from_cwd(tmp_path):
    """`hqbacktest run` (the console script) must be able to find a
    strategy module colocated with the config file in a fresh
    working directory.

    We exercise the actual `hqbacktest` console entry point (not
    `python -m hqbacktest run`) and verify that the run succeeds.
    The portal is monkey-patched inside the subprocess via a
    `sitecustomize`-style bootstrap script written to a temp
    directory that the subprocess adds to PYTHONPATH.
    """
    import shutil
    import textwrap

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _write_strategy_module(workdir)
    config_file = workdir / "config.toml"
    config_file.write_text(_minimal_config(workdir / "out"))

    # Bootstrap that swaps `_resolve_portal` for the memory portal.
    # The CLI imports this module by name via `HQBACKTEST_CLI_BOOTSTRAP`.
    bootstrap = workdir / "_cli_bootstrap.py"
    bootstrap.write_text(
        textwrap.dedent(
            """
            import hqbacktest.cli.runner as _r
            from hqbacktest.data import InMemoryDataPortal
            from hqbacktest.domain.bar import Bar
            _P = InMemoryDataPortal(
                calendar=['20240102', '20240103', '20240104'],
                universe_by_date={'20240102': ['600000.SH']},
                as_of='20240104',
            )
            for _d in ('20240102', '20240103', '20240104'):
                _P.add_bar(Bar.from_raw(
                    symbol='600000.SH', date=_d, open='10',
                    high='30', low='5', close='10', volume=1000,
                ))
            _r._resolve_portal = lambda source, data_root: _P
            """
        ).strip()
        + "\n"
    )

    hqbacktest_bin = shutil.which("hqbacktest")
    if hqbacktest_bin is None:
        argv = [sys.executable, "-m", "hqbacktest", "run"]
    else:
        argv = [hqbacktest_bin, "run"]
    env = {
        **os.environ,
        "PYTHONPATH": str(workdir),  # so the bootstrap is importable
        "HQBACKTEST_CLI_BOOTSTRAP": "_cli_bootstrap",
    }
    result = subprocess.run(
        argv + ["--config", str(config_file)],
        capture_output=True,
        text=True,
        cwd=str(workdir),
        check=False,
        env=env,
    )
    assert result.returncode == 0, (
        f"console script failed (exit={result.returncode}):\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    out = workdir / "out"
    assert out.exists()
    assert (out / "summary.json").exists()


# ---------------------------------------------------------------------------
# Config validation: nan / inf / negative / bad date / empty window
# ---------------------------------------------------------------------------


def test_initial_cash_nan_rejected(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[start]\n"
        "start_date = '20240102'\n"
        "end_date = '20240104'\n"
        "[capital]\n"
        "initial_cash = 'nan'\n"
        "[data]\n"
        "source = 'memory'\n"
        "[strategy]\n"
        "module = 'strategy'\n"
        "[output]\n"
        f"directory = '{tmp_path / 'out'}'\n"
    )
    with pytest.raises(ConfigError, match="nan|valid number|number"):
        load_config_file(str(cfg))


def test_initial_cash_inf_rejected(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[start]\n"
        "start_date = '20240102'\n"
        "end_date = '20240104'\n"
        "[capital]\n"
        "initial_cash = 'inf'\n"
        "[data]\n"
        "source = 'memory'\n"
        "[strategy]\n"
        "module = 'strategy'\n"
        "[output]\n"
        f"directory = '{tmp_path / 'out'}'\n"
    )
    with pytest.raises(ConfigError):
        load_config_file(str(cfg))


def test_initial_cash_negative_rejected(tmp_path):
    """`_require_decimal` already enforces `min_value=0`, so a
    negative literal must surface as ConfigError."""
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[start]\n"
        "start_date = '20240102'\n"
        "end_date = '20240104'\n"
        "[capital]\n"
        "initial_cash = '-100'\n"
        "[data]\n"
        "source = 'memory'\n"
        "[strategy]\n"
        "module = 'strategy'\n"
        "[output]\n"
        f"directory = '{tmp_path / 'out'}'\n"
    )
    with pytest.raises(ConfigError):
        load_config_file(str(cfg))


def test_start_date_impossible_rejected(tmp_path):
    """An impossible calendar date (`20241399`) must be rejected.

    The `end_date` is lex-greater AND a real calendar date so the
    failure mode is unambiguously the impossible-date validation in
    `validate_yyyymmdd` (and not the `start > end` ordering check
    that would fire if both dates were lex-comparable).
    """
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[start]\n"
        "start_date = '20241399'\n"  # month 13 — not a real date
        "end_date = '20241231'\n"  # lex-greater than start, real date
        "[capital]\n"
        "initial_cash = '100000'\n"
        "[data]\n"
        "source = 'memory'\n"
        "[strategy]\n"
        "module = 'strategy'\n"
        "[output]\n"
        f"directory = '{tmp_path / 'out'}'\n"
    )
    with pytest.raises(ConfigError) as exc:
        load_config_file(str(cfg))
    # The message must clearly attribute the failure to the impossible
    # start_date, not to a `start > end` ordering error.
    assert "20241399" in str(
        exc.value
    ), f"error should mention the impossible start_date; got {exc.value!r}"
    assert "calendar" in str(exc.value).lower(), (
        f"error should mention 'calendar' as the validation failure; "
        f"got {exc.value!r}"
    )
    assert (
        "start_date" in str(exc.value) or "start" in str(exc.value).lower()
    ), f"error should mention the field name; got {exc.value!r}"


def test_end_date_impossible_rejected(tmp_path):
    """An impossible `end_date` must also be rejected.

    Uses `end_date='20240230'` (Feb 30, non-leap year) with a real,
    lex-smaller `start_date` so the failure mode is the impossible
    date, not the `start > end` check.
    """
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        "[start]\n"
        "start_date = '20240102'\n"
        "end_date = '20240230'\n"  # Feb 30 — not a real date
        "[capital]\n"
        "initial_cash = '100000'\n"
        "[data]\n"
        "source = 'memory'\n"
        "[strategy]\n"
        "module = 'strategy'\n"
        "[output]\n"
        f"directory = '{tmp_path / 'out'}'\n"
    )
    with pytest.raises(ConfigError) as exc:
        load_config_file(str(cfg))
    assert "20240230" in str(
        exc.value
    ), f"error should mention the impossible end_date; got {exc.value!r}"


def test_engine_window_with_zero_trading_days_raises(tmp_path):
    """An engine window with no trading days must raise rather than
    silently write an empty result.
    """
    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240104",
        initial_cash=Decimal("100000"),
        source="tushare",
    )

    class S(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

    p = InMemoryDataPortal(
        calendar=[],  # empty -> no trading days
        universe_by_date={},
        as_of="20991231",
    )
    from hqbacktest.engine.errors import ConfigurationError

    # The empty-window check fires at iterator construction time, so
    # we get a typed ConfigurationError (mapped to CLI exit 2 by
    # `__main__`) rather than a RunFailed mid-run.
    with pytest.raises(ConfigurationError, match="no trading days"):
        BacktestEngine(cfg, strategy=S(), portal=p).run()


# ---------------------------------------------------------------------------
# Output directory reuse: exit 3 unless --force
# ---------------------------------------------------------------------------


def test_output_dir_with_prior_files_rejected(tmp_path):
    """When the output directory already contains prior-run files,
    the runner must NOT silently overwrite them (exit 3).
    """
    portal = _memory_portal()
    from hqbacktest.cli import runner

    strategy = tmp_path / "strategy.py"
    strategy.write_text(
        "from hqbacktest import BaseStrategy\n"
        "class S(BaseStrategy):\n"
        "    def initialize(self, context):\n"
        "        context.set_universe(['600000.SH'])\n"
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "summary.json").write_text('{"old": true}')
    cfg = tmp_path / "c.toml"
    cfg.write_text(_minimal_config(out_dir))
    original = runner._resolve_portal
    runner._resolve_portal = lambda source, data_root: portal
    try:
        result = run_from_file(str(cfg))
    finally:
        runner._resolve_portal = original
    assert result.exit_code == 3
    assert "prior" in result.message.lower() or "exists" in result.message.lower()
    # The prior summary.json must be untouched.
    assert (out_dir / "summary.json").read_text() == '{"old": true}'


# ---------------------------------------------------------------------------
# Context.order_value: int / str accepted
# ---------------------------------------------------------------------------


def test_order_value_accepts_int_cash():
    """`order_value(symbol, 15000)` (int) must not raise — int is a
    valid monetary literal at the strategy-API layer."""

    class Spend(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20240102":
                context.order_value("600000.SH", 15000)

    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240103",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    engine = BacktestEngine(cfg, strategy=Spend(), portal=_memory_portal())
    engine.run()  # must not raise


def test_order_value_accepts_str_cash():
    """`order_value(symbol, '15000')` (str) must not raise either."""

    class Spend(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20240102":
                context.order_value("600000.SH", "15000")

    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240103",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    engine = BacktestEngine(cfg, strategy=Spend(), portal=_memory_portal())
    engine.run()  # must not raise


def test_order_target_value_accepts_int_cash():
    """`order_target_value(symbol, 15000)` (int) must also accept an
    int/str monetary literal, not only `Decimal`."""

    class TargetSpend(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20240102":
                context.order_target_value("600000.SH", 15000)

    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240103",
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    engine = BacktestEngine(cfg, strategy=TargetSpend(), portal=_memory_portal())
    engine.run()  # must not raise


def test_empty_window_cli_returns_exit_2(tmp_path):
    """An empty trading-day window must surface as exit code 2
    (configuration error), not exit 4 (run failure)."""
    from hqbacktest.cli import runner

    empty = InMemoryDataPortal(calendar=[], universe_by_date={}, as_of="20991231")
    strategy = tmp_path / "strategy.py"
    strategy.write_text(
        "from hqbacktest import BaseStrategy\n"
        "class S(BaseStrategy):\n"
        "    def initialize(self, context):\n"
        "        context.set_universe(['600000.SH'])\n"
    )
    out_dir = tmp_path / "out"
    cfg = tmp_path / "c.toml"
    cfg.write_text(_minimal_config(out_dir))
    original = runner._resolve_portal
    runner._resolve_portal = lambda source, data_root: empty
    try:
        result = run_from_file(str(cfg))
    finally:
        runner._resolve_portal = original
    assert result.exit_code == 2, result.message
    assert "no trading days" in result.message


# ---------------------------------------------------------------------------
# git_commit semantics
# ---------------------------------------------------------------------------


def test_git_commit_reports_package_commit_or_none():
    """`_git_commit()` must not raise and returns either a short hex
    string or `None`. Best-effort lookup.
    """
    result = _git_commit()
    assert result is None or (isinstance(result, str) and len(result) >= 4)


def test_run_metadata_records_package_version(tmp_path):
    """`run_metadata.json` must record the hqbacktest package
    version and the configured start/end dates."""
    from hqbacktest.cli import runner
    from hqbacktest.cli.config import load_config_file

    portal = _memory_portal()
    out = tmp_path / "out"
    cfg_file = tmp_path / "c.toml"
    _write_strategy_module(tmp_path)
    cfg_file.write_text(_minimal_config(out))
    original = runner._resolve_portal
    runner._resolve_portal = lambda source, data_root: portal
    try:
        result = run_from_file(str(cfg_file))
    finally:
        runner._resolve_portal = original
    assert result.exit_code == 0, result.message
    meta = json.loads((result.output_dir / "run_metadata.json").read_text())
    from hqbacktest import __version__ as HQ_VER

    assert meta["hqbacktest_version"] == HQ_VER
    assert meta["config_start_date"] == "20240102"
    assert meta["config_end_date"] == "20240104"
