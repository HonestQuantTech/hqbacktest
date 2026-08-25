"""Command-line entry point: `hqbacktest run --config FILE --output DIR`.

Uses stdlib `argparse` (no new dependency). On any user-facing problem
we print a single readable line on stderr and exit with a non-zero code.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from typing import Optional, Sequence

from .config import ConfigError
from .runner import RunResult, _prepare_sys_path, run_from_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hqbacktest",
        description=("Run an A-share backtest from a TOML config file."),
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Execute a backtest from a TOML config")
    run.add_argument(
        "--config",
        required=True,
        help="Path to the TOML config file (required).",
    )
    run.add_argument(
        "--output",
        required=False,
        default=None,
        help=(
            "Output directory. When given, overrides [output].directory from "
            "the config. Created if missing; receives config.toml, "
            "run_metadata.json, events.jsonl, equity_curve.csv, orders.csv, "
            "fills.csv, positions.csv, costs.csv and summary.json."
        ),
    )
    run.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite an output directory that already contains " "prior-run files."
        ),
    )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.command is None:
        parser.print_help(sys.stderr)
        return 1

    if args.command == "run":
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    # Prepend the config file's directory and the current working directory
    # to `sys.path` so the strategy module can be resolved by name alone,
    # matching the documented "first-mile" workflow. This mirrors what
    # `python -m` would do for an in-tree import and makes the console
    # script behave the same way as `python -m hqbacktest run`.
    _prepare_sys_path(args.config)
    # Honor a test-only env hook so the CLI can be driven against an
    # in-memory portal in subprocess tests without touching the real
    # `~/.hqdata` snapshot.
    _maybe_load_test_bootstrap()
    try:
        result: RunResult = run_from_file(
            args.config, output_dir=args.output, force=args.force
        )
    except ConfigError as exc:
        print(f"hqbacktest: {exc}", file=sys.stderr)
        return 2

    if result.exit_code != 0:
        print(
            f"hqbacktest: {result.message or 'run failed'}",
            file=sys.stderr,
        )
        return result.exit_code

    print(f"hqbacktest: wrote results to {result.output_dir}")
    return 0


def _maybe_load_test_bootstrap() -> None:
    """If `HQBACKTEST_CLI_BOOTSTRAP` is set, import that module by name.

    Test-only hook used by `tests/cli/test_cli_validation.py` to swap
    the portal builder in a subprocess without writing to the real
    `~/.hqdata` snapshot. Production users never set this.
    """
    name = os.environ.get("HQBACKTEST_CLI_BOOTSTRAP")
    if not name:
        return
    importlib.import_module(name)


__all__ = ["build_parser", "main"]
