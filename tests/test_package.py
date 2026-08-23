"""Smoke tests for the hqbacktest package (tasks 2 + 3).

These tests must run without network access, data source credentials, or
any non-standard-library imports beyond pytest.
"""


def test_import_hqbacktest():
    """The package should be importable without side effects."""
    import hqbacktest

    assert hqbacktest is not None


def test_version_is_non_empty_string():
    """`__version__` should be a non-empty string matching pyproject.toml."""
    import hqbacktest

    assert isinstance(hqbacktest.__version__, str)
    assert hqbacktest.__version__


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
