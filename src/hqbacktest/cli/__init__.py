"""Command-line interface for hqbacktest (task 12).

The CLI is intentionally tiny: it loads a TOML config file, validates it,
constructs a `BacktestConfig` and a strategy class, runs the backtest, and
writes the result to an output directory. Heavy lifting lives in
`engine` / `data` / `domain`; this package only orchestrates.
"""
