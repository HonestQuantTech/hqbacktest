"""Runner: build engine, run, write per-run output directory (task 12).

The runner is invoked by `hqbacktest run --config <file> --output <dir>`.
It is also usable directly (`run_from_file(path)`) for tests.

Output directory layout (one dir per run, created if missing)::

    <output_dir>/
    ├── config.toml         # the exact config the user supplied
    ├── run_metadata.json   # package version, python, source, git
    ├── events.jsonl        # engine event log (audit trail)
    ├── equity_curve.csv
    ├── orders.csv
    ├── fills.csv
    ├── positions.csv
    ├── costs.csv
    └── summary.json        # config + metrics + diagnostics

The runner NEVER writes tokens, full env vars, or local absolute paths.
`data_root` is recorded as the configured value (default `~/.hqdata`);
the tilde form is kept as-is, never expanded to a machine-specific path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..__init__ import __version__ as HQBACKTEST_VERSION
from ..data.hqdata_portal import HqDataCsvPortal, resolve_source_location
from .config import (
    ConfigError,
    ConfigFile,
    build_backtest_config,
    load_config_file,
    resolve_strategy,
)


# --------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------- #


def run_from_file(
    config_path: str, output_dir: Optional[str] = None, force: bool = False
) -> "RunResult":  # type: ignore[name-defined]
    """Load the config, build the engine, run, and write the output dir.

    `output_dir` (from the CLI `--output` flag) overrides the config's
    `[output].directory` when given. Returns a `RunResult` with the exit
    code (0 on success) and the path of the output directory. The CLI
    converts this to a process exit status. `force=True` lets the
    runner overwrite a non-empty output directory (task 20).

    Task 20: prepend the config's directory and cwd to `sys.path` so
    the user-supplied strategy module can be imported by name alone
    (the documented first-mile workflow).
    """
    _prepare_sys_path(config_path)
    config_file = load_config_file(config_path)
    return run_from_config(
        config_file, source_path=config_path, output_dir=output_dir, force=force
    )


def run_from_config(
    config_file: ConfigFile,
    *,
    source_path: Optional[str] = None,
    output_dir: Optional[str] = None,
    force: bool = False,
) -> "RunResult":  # type: ignore[name-defined]
    """Build the engine from a validated `ConfigFile` and run the backtest.

    `force=True` lets the run overwrite an output directory that
    already contains prior-run files (task 20). Without `force`,
    mixing a fresh run with stale CSVs / summary.json from a
    previous run is rejected with exit code 3 to keep the audit
    trail honest.
    """
    effective_output = output_dir or config_file.output_directory
    try:
        strategy = resolve_strategy(config_file)
    except ConfigError as exc:
        return RunResult(exit_code=2, output_dir=None, message=str(exc))
    backtest_config = build_backtest_config(config_file)
    portal = _resolve_portal(backtest_config.source, backtest_config.data_root)
    output_path = Path(effective_output)
    if output_path.exists() and not output_path.is_dir():
        # The configured output path is an existing FILE, not a
        # directory. Refuse rather than silently failing later.
        return RunResult(
            exit_code=3,
            output_dir=None,
            message=(
                f"output path {output_path!s} is not a directory; "
                f"remove the file or change [output].directory"
            ),
        )
    if output_path.exists() and not force:
        # Reject if the directory already holds files from a prior run;
        # an empty directory is allowed (first run).
        if any(output_path.iterdir()):
            return RunResult(
                exit_code=3,
                output_dir=output_path,
                message=(
                    f"output directory {output_path!s} already contains "
                    f"prior-run files; pass force=True to overwrite or "
                    f"choose a fresh directory"
                ),
            )
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return RunResult(
            exit_code=3,
            output_dir=None,
            message=(f"cannot create output directory {output_path!s}: {exc}"),
        )
    if not os.access(output_path, os.W_OK):
        return RunResult(
            exit_code=3,
            output_dir=output_path,
            message=(f"output directory {output_path!s} is not writable"),
        )

    from ..engine.engine import BacktestEngine  # local: avoid circular
    from ..engine.errors import ConfigurationError

    try:
        engine = BacktestEngine(backtest_config, strategy=strategy, portal=portal)
        result = engine.run()
    except ConfigurationError as exc:
        # A configuration error (e.g. an empty trading-day window) is a
        # user-input problem, not a run failure: exit 2, single line.
        return RunResult(exit_code=2, output_dir=output_path, message=str(exc))
    except Exception as exc:
        # We do NOT swallow this as a normal RunFailed; the CLI surfaces
        # the message verbatim.
        return RunResult(
            exit_code=4,
            output_dir=output_path,
            message=f"backtest run failed: {exc}",
        )

    # Persist everything.
    _write_config_toml(output_path, config_file, source_path)
    _write_run_metadata(
        output_path, config_file, backtest_config, source_path, effective_output
    )
    result.save(str(output_path))

    # Task 19: warn the operator when factor diagnostics surfaced a
    # holding-period jump. The NAV excludes dividends (policy="none"),
    # so this is the only place a human sees the bias. The CLI is the
    # operator's terminal; we print one summary line.
    holdings_diag = result.factor_diagnostics or []
    if holdings_diag:
        print(
            f"warning: {len(holdings_diag)} corporate-action factor "
            f"jumps detected during holding periods; NAV excludes "
            f"dividends (adjustment_policy=none), see summary.json",
            flush=True,
        )

    return RunResult(exit_code=0, output_dir=output_path, message="")


class RunResult:
    """The CLI's structured view of a single run."""

    def __init__(self, exit_code: int, output_dir: Optional[Path], message: str):
        self.exit_code = exit_code
        self.output_dir = output_dir
        self.message = message


# --------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------- #


def _resolve_portal(source: str, data_root: str):
    """Wrap `resolve_source_location` errors as `ConfigError`."""
    try:
        resolved_root, name = resolve_source_location(
            source, default_data_root=data_root
        )
    except Exception as exc:
        raise ConfigError(f"invalid data source {source!r}: {exc}") from exc
    return HqDataCsvPortal(source=name, data_root=resolved_root)


def _write_config_toml(
    output_dir: Path,
    config_file: ConfigFile,
    source_path: Optional[str],
) -> None:
    """Write the exact user-supplied config text to the output dir.

    This is the audit trail: anyone can re-run from this file.
    """
    if source_path is None:
        return
    src = Path(source_path)
    if not src.exists() or not src.is_file():
        return
    try:
        text = src.read_text(encoding="utf-8")
    except OSError:
        return
    (output_dir / "config.toml").write_text(text, encoding="utf-8")


def _write_run_metadata(
    output_dir: Path,
    config_file: ConfigFile,
    backtest_config,
    source_path: Optional[str],
    effective_output: str,
) -> None:
    """Record package/python/source/git info so the run is self-describing.

    Task 22.2: the path fields (`config_path`, `output_directory`,
    `config_output_directory`) are persisted as **relative paths**
    (relative to the run-time cwd) so the run is reproducible across
    machines without leaking `/home/<user>` style directory layouts.
    `data_root` is kept as the user-supplied value because it is part
    of the configuration snapshot and may intentionally be absolute.
    """
    metadata: Dict[str, Any] = {
        "hqbacktest_version": HQBACKTEST_VERSION,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config_path": _relativize_path(source_path),
        "config_start_date": config_file.start_date,
        "config_end_date": config_file.end_date,
        "config_initial_cash": str(config_file.initial_cash),
        "config_source": config_file.source,
        "config_strategy_module": config_file.strategy_module,
        "config_strategy_class": config_file.strategy_class,
        "output_directory": _relativize_path(effective_output),
        "config_output_directory": _relativize_path(config_file.output_directory),
        "data_root": backtest_config.data_root,
        "adjustment_policy": backtest_config.adjustment_policy,
        "git_commit": _git_commit(),
    }
    (output_dir / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _relativize_path(path_str: Optional[str]) -> Optional[str]:
    """Return `path_str` as a path relative to the run-time cwd.

    Task 22.2: prevents `run_metadata.json` from carrying absolute
    paths like `/home/<user>/run.toml` or
    `/home/<user>/results/run-1`. Falls back to the bare filename when
    the path cannot be relativized (so we never crash the runner nor
    leak an absolute path). Returns `None` unchanged when `path_str`
    is `None`.

    Implementation note: `os.path.relpath` is intentional rather than
    `pathlib.Path.relative_to` because the former gracefully handles
    paths outside cwd (producing `../...` relpaths), whereas the
    latter raises `ValueError`.
    """
    if path_str is None:
        return None
    try:
        cwd = os.getcwd()
        return os.path.relpath(path_str, start=cwd)
    except (OSError, ValueError):
        # Path resolution failed (e.g. cross-drive path on Windows);
        # fall back to the bare filename so we neither crash the runner
        # nor write an absolute path into run_metadata.json.
        return os.path.basename(path_str)


def _git_commit() -> Optional[str]:
    """Return the hqbacktest package's own short git commit, or `None`
    if unavailable.

    Task 20: the commit recorded here is the commit of the
    hqbacktest repo, NOT the user's cwd repository. A user
    running the CLI from inside their own strategy repo gets
    hqbacktest's commit (the engine that produced the result);
    if they want their strategy's commit too they can record it
    themselves in the config. We never raise from here; failure
    to read git is a no-op.
    """
    try:
        import hqbacktest as _hq_pkg

        pkg_dir = Path(_hq_pkg.__file__).resolve().parent
        # Walk up to find the directory that contains `.git`.
        for parent in [pkg_dir, *pkg_dir.parents]:
            if (parent / ".git").exists():
                out = subprocess.check_output(
                    ["git", "-C", str(parent), "rev-parse", "--short", "HEAD"],
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                )
                text = out.decode("utf-8").strip()
                return text or None
        return None
    except Exception:
        return None


def _prepare_sys_path(config_path: str) -> None:
    """Add the config file's directory and cwd to `sys.path`.

    Task 20: lets the user-supplied strategy module be imported by
    name alone (e.g. `module = 'strategy'`) without having to add
    `sys.path` boilerplate in the strategy file. Mirrors what
    `python -m hqbacktest run` would do for in-tree imports.
    """
    candidates: List[str] = []
    try:
        cfg_dir = str(Path(config_path).resolve().parent)
        if cfg_dir and cfg_dir not in candidates:
            candidates.append(cfg_dir)
    except OSError:
        pass
    try:
        cwd = str(Path.cwd())
        if cwd and cwd not in candidates:
            candidates.append(cwd)
    except OSError:
        pass
    for entry in candidates:
        if entry and entry not in sys.path:
            sys.path.insert(0, entry)


__all__ = [
    "RunResult",
    "run_from_config",
    "run_from_file",
]
