"""Data portal errors.

All data-portal related failures derive from `DataError` so engine code can
catch the family in one place. The exception types follow contract §6 rule 1
("any future-data access must raise") and rule 13 ("DataView reads past
`visible_through` must throw immediately").
"""


class DataError(Exception):
    """Base class for every error raised by the data layer."""


class FutureDataAccessError(DataError):
    """A read was attempted past the configured `visible_through` cutoff."""

    def __init__(self, requested: str, visible_through: str) -> None:
        super().__init__(
            f"future data access: requested {requested}, "
            f"visible_through={visible_through}"
        )
        self.requested = requested
        self.visible_through = visible_through


class MissingDataError(DataError):
    """Requested data is not available (no rows, missing dates, etc.)."""

    def __init__(self, what: str, detail: str = "") -> None:
        message = f"missing data: {what}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
        self.what = what
        self.detail = detail


class InvalidDataError(DataError):
    """Data returned by the source violates hqbacktest invariants."""

    def __init__(self, what: str, detail: str = "") -> None:
        message = f"invalid data: {what}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
        self.what = what
        self.detail = detail


class SourceNotInitializedError(DataError):
    """A portal method was called before the underlying source was initialized."""


class UnknownSymbolError(DataError):
    """A symbol does not exist in the universe for the requested date."""
