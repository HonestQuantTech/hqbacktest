"""CLI tests for `hqbacktest run --config FILE --output DIR` (task 12).

Covers:
    * Config file validation (errors, missing keys, unknown sections)
    * End-to-end run that produces the per-run output directory
    * Reproducibility (same input -> identical tables)
    * `python -m hqbacktest` and `hqbacktest` console-script entry points
    * Error messages and non-zero exit codes
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from hqbacktest import __version__ as HQBACKTEST_VERSION
from hqbacktest.cli.__main__ import main, build_parser
from hqbacktest.cli.config import (
    ConfigError,
    build_backtest_config,
    load_config_file,
    resolve_strategy,
)
from hqbacktest.cli.runner import run_from_file


# --------------------------------------------------------------------- #
# Config file parsing / validation
# --------------------------------------------------------------------- #


def _write(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_config_load_minimal(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "c.toml",
        """
        [start]
        start_date = "20240102"
        end_date = "20240104"
        [capital]
        initial_cash = "100000"
        [data]
        source = "tushare"
        [strategy]
        module = "examples.buy_and_hold"
        [output]
        directory = "{out}"
        """.format(
            out=str(tmp_path / "out")
        ),
    )
    cf = load_config_file(str(cfg_path))
    assert cf.start_date == "20240102"
    assert cf.end_date == "20240104"
    assert cf.initial_cash == 100000
    assert cf.source == "tushare"
    assert cf.strategy_module == "examples.buy_and_hold"
    assert cf.strategy_class is None
    assert cf.output_directory == str(tmp_path / "out")
    assert cf.raw_text  # exact bytes preserved for the audit trail


def test_config_rejects_missing_start_date(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "c.toml",
        """
        [start]
        end_date = "20240104"
        [capital]
        initial_cash = "100000"
        [data]
        source = "tushare"
        [strategy]
        module = "examples.buy_and_hold"
        [output]
        directory = "out"
        """,
    )
    with pytest.raises(ConfigError, match="missing required key 'start_date'"):
        load_config_file(str(cfg_path))


def test_config_rejects_unknown_section(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "c.toml",
        """
        [start]
        start_date = "20240102"
        end_date = "20240104"
        [capital]
        initial_cash = "100000"
        [data]
        source = "tushare"
        [strategy]
        module = "examples.buy_and_hold"
        [output]
        directory = "out"
        [extra]
        foo = 1
        """,
    )
    with pytest.raises(ConfigError, match="unknown config sections"):
        load_config_file(str(cfg_path))


def test_config_rejects_invalid_date_format(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "c.toml",
        """
        [start]
        start_date = "2024-01-02"
        end_date = "20240104"
        [capital]
        initial_cash = "100000"
        [data]
        source = "tushare"
        [strategy]
        module = "examples.buy_and_hold"
        [output]
        directory = "out"
        """,
    )
    with pytest.raises(ConfigError, match="must be 8 digits"):
        load_config_file(str(cfg_path))


def test_config_rejects_negative_cash(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "c.toml",
        """
        [start]
        start_date = "20240102"
        end_date = "20240104"
        [capital]
        initial_cash = "-1000"
        [data]
        source = "tushare"
        [strategy]
        module = "examples.buy_and_hold"
        [output]
        directory = "out"
        """,
    )
    with pytest.raises(ConfigError, match="must be >="):
        load_config_file(str(cfg_path))


def test_config_rejects_nonexistent_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        load_config_file(str(tmp_path / "nope.toml"))


def test_config_parses_optional_cost_overrides(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "c.toml",
        """
        [start]
        start_date = "20240102"
        end_date = "20240104"
        [capital]
        initial_cash = "100000"
        [data]
        source = "tushare"
        [strategy]
        module = "examples.buy_and_hold"
        [cost_model]
        commission_rate = "0.0003"
        min_commission = "10.00"
        stamp_tax_rate = "0.001"
        transfer_fee_rate = "0.0"
        [output]
        directory = "out"
        """,
    )
    cf = load_config_file(str(cfg_path))
    bc = build_backtest_config(cf)
    from decimal import Decimal

    assert bc.cost_model.commission_rate == Decimal("0.0003")
    assert bc.cost_model.min_commission == Decimal("10.00")
    assert bc.cost_model.stamp_tax_rate == Decimal("0.001")
    assert bc.cost_model.transfer_fee_rate == Decimal("0.0")


def test_config_rejects_invalid_toml(tmp_path: Path) -> None:
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text("this is not = valid toml [", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config_file(str(cfg_path))


# --------------------------------------------------------------------- #
# Strategy resolution
# --------------------------------------------------------------------- #


def test_resolve_strategy_uses_class_name(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "c.toml",
        """
        [start]
        start_date = "20240102"
        end_date = "20240104"
        [capital]
        initial_cash = "100000"
        [data]
        source = "tushare"
        [strategy]
        module = "examples.buy_and_hold"
        class_name = "BuyAndHold"
        [output]
        directory = "out"
        """,
    )
    cf = load_config_file(str(cfg_path))
    strategy = resolve_strategy(cf)
    from examples.buy_and_hold import BuyAndHold

    assert isinstance(strategy, BuyAndHold)


def test_resolve_strategy_uses_kwargs(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "c.toml",
        """
        [start]
        start_date = "20240102"
        end_date = "20240104"
        [capital]
        initial_cash = "100000"
        [data]
        source = "tushare"
        [strategy]
        module = "tests.examples.strategies"
        class_name = "KwargReceivingStrategy"
        [strategy.kwargs]
        answer = 42
        [output]
        directory = "out"
        """,
    )
    cf = load_config_file(str(cfg_path))
    strategy = resolve_strategy(cf)
    assert strategy.received() == {"answer": 42}


def test_resolve_strategy_fails_for_missing_class(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "c.toml",
        """
        [start]
        start_date = "20240102"
        end_date = "20240104"
        [capital]
        initial_cash = "100000"
        [data]
        source = "tushare"
        [strategy]
        module = "examples.buy_and_hold"
        class_name = "DoesNotExist"
        [output]
        directory = "out"
        """,
    )
    cf = load_config_file(str(cfg_path))
    with pytest.raises(ConfigError, match="no attribute 'DoesNotExist'"):
        resolve_strategy(cf)


def test_resolve_strategy_fails_for_non_subclass(tmp_path: Path) -> None:
    cfg_path = _write(
        tmp_path / "c.toml",
        """
        [start]
        start_date = "20240102"
        end_date = "20240104"
        [capital]
        initial_cash = "100000"
        [data]
        source = "tushare"
        [strategy]
        module = "tests.examples.strategies"
        class_name = "NotAStrategy"
        [output]
        directory = "out"
        """,
    )
    cf = load_config_file(str(cfg_path))
    with pytest.raises(ConfigError, match="not a BaseStrategy subclass"):
        resolve_strategy(cf)


# --------------------------------------------------------------------- #
# End-to-end: full backtest via the in-process API
# --------------------------------------------------------------------- #


def _minimal_config(output_dir: Path) -> str:
    return f"""
    [start]
    start_date = "20240102"
    end_date = "20240104"
    [capital]
    initial_cash = "100000"
    [data]
    source = "tushare"
    [strategy]
    module = "examples.buy_and_hold"
    [output]
    directory = "{output_dir}"
    """


def test_runner_writes_all_expected_files(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_minimal_config(tmp_path / "out"), encoding="utf-8")
    result = run_from_file(str(cfg_path))
    assert result.exit_code == 0, result.message
    out = result.output_dir
    for filename in (
        "config.toml",
        "run_metadata.json",
        "events.jsonl",
        "equity_curve.csv",
        "orders.csv",
        "fills.csv",
        "positions.csv",
        "costs.csv",
        "summary.json",
    ):
        assert (out / filename).exists(), f"missing {filename}"


def test_runner_writes_run_metadata(tmp_path: Path) -> None:
    import json

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_minimal_config(tmp_path / "out"), encoding="utf-8")
    result = run_from_file(str(cfg_path))
    assert result.exit_code == 0
    meta = json.loads((result.output_dir / "run_metadata.json").read_text())
    assert meta["hqbacktest_version"] == HQBACKTEST_VERSION
    assert meta["config_start_date"] == "20240102"
    assert meta["config_end_date"] == "20240104"
    assert meta["config_initial_cash"] == "100000"
    assert meta["config_source"] == "tushare"
    assert meta["config_strategy_module"] == "examples.buy_and_hold"
    assert meta["adjustment_policy"] == "none"
    # git commit is either set (if the repo has one) or absent.
    assert "git_commit" in meta


def test_runner_does_not_write_secrets(tmp_path: Path, monkeypatch) -> None:
    """Tokens, env vars, and absolute local paths must never appear in
    the output directory."""
    monkeypatch.setenv("HQBACKTEST_TEST_TOKEN", "super-secret-token-12345")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_minimal_config(tmp_path / "out"), encoding="utf-8")
    result = run_from_file(str(cfg_path))
    assert result.exit_code == 0
    for path in result.output_dir.iterdir():
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "super-secret-token-12345" not in text, f"secret leaked into {path}"
        assert "HQBACKTEST_TEST_TOKEN" not in text, f"env var leaked into {path}"


def test_runner_is_deterministic(tmp_path: Path) -> None:
    """Two runs with the same config and same data produce identical tables."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "config.toml").write_text(_minimal_config(a / "out"), encoding="utf-8")
    (b / "config.toml").write_text(_minimal_config(b / "out"), encoding="utf-8")
    res_a = run_from_file(str(a / "config.toml"))
    res_b = run_from_file(str(b / "config.toml"))
    assert res_a.exit_code == 0
    assert res_b.exit_code == 0
    files = [
        "equity_curve.csv",
        "orders.csv",
        "fills.csv",
        "positions.csv",
        "costs.csv",
        "summary.json",
    ]
    for fn in files:
        assert (a / "out" / fn).read_text() == (b / "out" / fn).read_text()


def test_runner_creates_output_directory(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    out_dir = tmp_path / "does" / "not" / "exist"
    cfg_path.write_text(_minimal_config(out_dir), encoding="utf-8")
    result = run_from_file(str(cfg_path))
    assert result.exit_code == 0
    assert result.output_dir.exists()


def test_runner_output_override_beats_config_directory(tmp_path: Path) -> None:
    """`--output` (the `output_dir` arg) overrides `[output].directory`."""
    import json

    config_out = tmp_path / "from_config"
    override_out = tmp_path / "from_cli_flag"
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_minimal_config(config_out), encoding="utf-8")
    result = run_from_file(str(cfg_path), output_dir=str(override_out))
    assert result.exit_code == 0, result.message
    assert result.output_dir == override_out
    assert (override_out / "equity_curve.csv").exists()
    assert not config_out.exists()
    meta = json.loads((override_out / "run_metadata.json").read_text())
    assert meta["output_directory"] == str(override_out)
    assert meta["config_output_directory"] == str(config_out)


def test_runner_unwritable_output_returns_3(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    # Point output at an existing file (not a directory). `mkdir` with
    # `parents=True, exist_ok=True` on an existing file raises
    # `FileExistsError` (an `OSError`), so the runner must return exit 3.
    bad_output = tmp_path / "i_am_a_file"
    bad_output.write_text("not a dir", encoding="utf-8")
    cfg_path.write_text(_minimal_config(bad_output), encoding="utf-8")
    result = run_from_file(str(cfg_path))
    assert result.exit_code == 3, result.message


# --------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------- #


def test_cli_help_exits_0(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "hqbacktest" in (captured.out + captured.err)


def test_cli_runs_end_to_end(tmp_path: Path) -> None:
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text(_minimal_config(tmp_path / "out"), encoding="utf-8")
    rc = main(["run", "--config", str(cfg_path), "--output", str(tmp_path / "out")])
    assert rc == 0
    assert (tmp_path / "out" / "equity_curve.csv").exists()


def test_cli_run_without_output_uses_config_directory(tmp_path: Path) -> None:
    """`--output` is optional; without it the config's directory is used."""
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text(_minimal_config(tmp_path / "out"), encoding="utf-8")
    rc = main(["run", "--config", str(cfg_path)])
    assert rc == 0
    assert (tmp_path / "out" / "equity_curve.csv").exists()


def test_cli_returns_2_on_config_error(tmp_path: Path, capsys) -> None:
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text("[start]\n", encoding="utf-8")
    rc = main(["run", "--config", str(cfg_path), "--output", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "hqbacktest:" in captured.err


def test_console_script_runs_end_to_end(tmp_path: Path) -> None:
    """`hqbacktest` console script works from a fresh subprocess.

    We invoke the script via `python -m hqbacktest run` so the test does
    not depend on the console-script being installed on the test machine.
    """
    repo = Path(__file__).resolve().parents[2]
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text(_minimal_config(tmp_path / "out"), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hqbacktest",
            "run",
            "--config",
            str(cfg_path),
            "--output",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        cwd=str(repo),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "out" / "equity_curve.csv").exists()
