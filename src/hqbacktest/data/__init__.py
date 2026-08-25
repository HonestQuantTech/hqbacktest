"""hqbacktest data layer.

Re-exports the public API of the data package so callers can write:

    from hqbacktest.data import DataView, MarketDataPortal, InMemoryDataPortal
"""

from .cache import CacheKey, DataCache
from .data_view import DataView
from .errors import (
    DataError,
    FutureDataAccessError,
    InvalidDataError,
    MissingDataError,
    SnapshotFileMissingError,
    UnknownSymbolError,
)
from .hqdata_portal import (
    DEFAULT_DATA_ROOT,
    HqDataCsvPortal,
    resolve_source_location,
)
from .memory_portal import InMemoryDataPortal
from .portal import DataVersion, MarketDataPortal
from .validators import (
    SENTINEL_NO_HISTORY,
    assert_unique_sorted,
    require_columns,
    validate_decimal_series,
    validate_symbol,
    validate_yyyymmdd,
)

__all__ = [
    "CacheKey",
    "DEFAULT_DATA_ROOT",
    "DataCache",
    "DataError",
    "DataVersion",
    "DataView",
    "FutureDataAccessError",
    "HqDataCsvPortal",
    "InMemoryDataPortal",
    "InvalidDataError",
    "MarketDataPortal",
    "MissingDataError",
    "SENTINEL_NO_HISTORY",
    "SnapshotFileMissingError",
    "UnknownSymbolError",
    "assert_unique_sorted",
    "require_columns",
    "resolve_source_location",
    "validate_decimal_series",
    "validate_symbol",
    "validate_yyyymmdd",
]
