"""Smoke tests for the hqbacktest package (tasks 2 + 3).

These tests must run without network access, data source credentials, or
any non-standard-library imports beyond pytest.
"""


def test_import_hqbacktest():
    """The package should be importable without side effects."""
    import hqbacktest

    assert hqbacktest is not None


def test_version_is_non_empty_string():
    """`__version__` should be a non-empty string."""
    import hqbacktest

    assert isinstance(hqbacktest.__version__, str)
    assert hqbacktest.__version__


def test_version_matches_pyproject():
    """`hqbacktest.__version__` MUST match the `version` field in
    `pyproject.toml`.

    Task 22 / 23 review (2026-08-25): the prior version-sync guard
    was `test_version_is_non_empty_string`, which only asserted that
    `__version__` was a non-empty string. The docstring claimed it
    matched `pyproject.toml` but the body never actually compared
    the two — that gap allowed `src/hqbacktest/__init__.py::__version__`
    to stay at `0.1.1` for two release cycles (v0.1.2, v0.1.3) even
    after `pyproject.toml` advanced, which would have made every
    `run_metadata.json` record the wrong engine version.

    This test parses `[project].version` from the in-repo
    `pyproject.toml` and asserts byte equality with the package
    `__version__`. The dependency on `tomllib` is stdlib on Python
    ≥ 3.11 (project's `requires-python` is `>=3.10`); on 3.10 we
    fall back to the `tomli` runtime dep already declared in
    `pyproject.toml`.
    """
    import re
    from pathlib import Path

    import hqbacktest

    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject_path.read_text(encoding="utf-8")
    # Minimal parser — avoid pulling a TOML lib just for one literal.
    # `pyproject.toml` is small and well-formed; this regex finds the
    # `[project]` table and pulls its `version = "X.Y.Z"` line. If the
    # layout ever changes (e.g. `version` moves to a sub-table), the
    # `None` fallback below will surface a clear error.
    match = re.search(
        r'^\[project\][^\[]*?version\s*=\s*["\']([^"\']+)["\']',
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, (
        f"could not locate `version = " "in [project] of {pyproject_path}"
    )
    pyproject_version = match.group(1)
    assert hqbacktest.__version__ == pyproject_version, (
        f"version drift: hqbacktest.__version__={hqbacktest.__version__!r} "
        f"but pyproject.toml [project].version={pyproject_version!r}; "
        f"both must be updated together when releasing"
    )


def test_public_api_includes_domain_models():
    """After task 9, the package re-exports domain, data and engine layers."""
    import hqbacktest

    expected = {
        "__version__",
        "AccountSnapshot",
        "AdjustmentPolicy",
        "BacktestConfig",
        "BacktestEngine",
        "BacktestResult",
        "Bar",
        "BaseStrategy",
        "CacheKey",
        "Context",
        "CorporateAction",
        "CorporateActionAdjustment",
        "CorporateActionProvider",
        "CostModel",
        "DataCache",
        "DataVersion",
        "DataView",
        "DefaultCostModel",
        "EngineEvent",
        "EquityPoint",
        "EventLog",
        "EventType",
        "FactorDiagnostic",
        "FactorDiagnosticCollector",
        "Fill",
        "HqDataCsvPortal",
        "InMemoryDataPortal",
        "MarketDataPortal",
        "MetricsConfig",
        "NullStrategy",
        "Order",
        "OrderStatus",
        "OrderType",
        "PerformanceMetrics",
        "Portfolio",
        "Position",
        "PositionSnapshot",
        "PriceMode",
        "RejectReason",
        "Side",
        "SimulatedBroker",
        "Strategy",
        "TradingDayIterator",
        "TradingRuleSet",
        "resolve_source_location",
    }
    assert set(hqbacktest.__all__) == expected
