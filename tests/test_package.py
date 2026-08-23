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
    """After task 4, the package re-exports the domain + data models."""
    import hqbacktest

    expected = {
        "__version__",
        "AccountSnapshot",
        "Bar",
        "CacheKey",
        "CorporateActionAdjustment",
        "DataCache",
        "DataVersion",
        "DataView",
        "EventType",
        "Fill",
        "HqDataCsvPortal",
        "InMemoryDataPortal",
        "MarketDataPortal",
        "Order",
        "OrderStatus",
        "OrderType",
        "Portfolio",
        "Position",
        "PositionSnapshot",
        "PriceMode",
        "RejectReason",
        "Side",
        "resolve_source_location",
    }
    assert set(hqbacktest.__all__) == expected
