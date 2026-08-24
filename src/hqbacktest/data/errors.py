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
    """Requested data is not available (no rows, missing dates, etc.).

    Use this for ordinary business-level absences: a symbol suspended on a
    given trading day, an IPO that has not started yet, a stock that has
    already delisted, etc. These are recoverable per-symbol outcomes and the
    engine must NOT abort the run because of them.

    For infrastructure-level failures (the whole daily snapshot file is
    missing on disk), raise `SnapshotFileMissingError` instead so the engine
    can distinguish "this stock has no row today" from "we cannot read any
    row at all today".
    """

    def __init__(self, what: str, detail: str = "") -> None:
        message = f"missing data: {what}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
        self.what = what
        self.detail = detail


class SnapshotFileMissingError(MissingDataError):
    """A whole-daily snapshot file is missing on disk (data infrastructure).

    Distinct from `MissingDataError` (a per-symbol gap such as a suspended or
    delisted stock) so the engine and broker can refuse to silently fold
    an infrastructure failure into a business rejection. The engine must
    abort the run with a clear `DATA_ERROR` rather than treating the
    missing file as "no quote available".

    `path` is the filesystem path that was expected; `what` describes the
    snapshot family (e.g. `stock_daily`).
    """

    def __init__(self, what: str, path: str) -> None:
        super().__init__(what, f"snapshot file missing: {path}")
        self.path = path


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
