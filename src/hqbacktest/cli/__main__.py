"""Command-line entry point: `hqbacktest run --config FILE --output DIR`.

Uses stdlib `argparse` (no new dependency). On any user-facing problem
we print a single readable line on stderr and exit with a non-zero code.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

from .config import ConfigError
from .runner import RunResult, run_from_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hqbacktest",
        description=(
            "Run an A-share backtest from a TOML config file. v0.1 ships a "
            "complete task 1-11 engine; this CLI is the documented end-user "
            "entry point (task 12)."
        ),
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
    try:
        result: RunResult = run_from_file(args.config, output_dir=args.output)
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


__all__ = ["build_parser", "main"]
