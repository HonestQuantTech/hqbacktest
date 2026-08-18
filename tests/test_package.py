"""Smoke tests for the hqbacktest package skeleton (task 2).

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


def test_public_api_limited_to_version():
    """In the v0.1 task 2 skeleton, only `__version__` is exported."""
    import hqbacktest

    assert set(hqbacktest.__all__) == {"__version__"}