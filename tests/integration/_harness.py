"""Shared helpers + auto-skip for the integration smoke tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

DEFAULT_DATA_ROOT = "~/.hqdata"
DEFAULT_SOURCE = "tushare"

# Calibration snapshot window (139 trading days for the
# `~/.hqdata/tushare` snapshot).
CALIBRATION_START = "20260105"
CALIBRATION_END = "20260731"

# Known dividend ex-date (600000.SH, factor 16.5935 -> 17.3774).
CALIBRATION_EX_DATE = "20260716"

# Known suspended window (000008.SZ).
CALIBRATION_SUSPENDED_START = "20260707"
CALIBRATION_SUSPENDED_END = "20260713"


def _data_root() -> Path:
    """Return the resolved hqdata root (overridable via HQDATA_ROOT)."""
    raw = os.environ.get("HQDATA_ROOT", DEFAULT_DATA_ROOT)
    return Path(raw).expanduser()


def _source_available(name: str = DEFAULT_SOURCE) -> bool:
    """True iff the source's CSV directory exists and looks non-empty."""
    root = _data_root() / name
    if not root.exists() or not root.is_dir():
        return False
    if not (root / "calendar.csv").exists():
        return False
    try:
        next(root.iterdir())
    except StopIteration:
        return False  # empty directory → snapshot is incomplete
    return True


def skip_if_no_snapshot() -> pytest.MarkDecorator:
    """Skip the test if the local `~/.hqdata/tushare` snapshot is missing.

    Use on every real-data test:
        @pytest.mark.integration
        @skip_if_no_snapshot()
        def test_xxx(): ...
    """
    reason = "hqdata snapshot not available at ~/.hqdata/tushare"
    return pytest.mark.skipif(not _source_available(), reason=reason)


__all__ = [
    "DEFAULT_DATA_ROOT",
    "DEFAULT_SOURCE",
    "CALIBRATION_START",
    "CALIBRATION_END",
    "CALIBRATION_EX_DATE",
    "CALIBRATION_SUSPENDED_START",
    "CALIBRATION_SUSPENDED_END",
    "_data_root",
    "_source_available",
    "skip_if_no_snapshot",
]
