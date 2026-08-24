"""Domain-layer ledger errors.

These errors subclass `ValueError` so existing callers keep working, but give
the engine a *typed* signal for business rejections (contract rule: every
rejection must carry a traceable reason). Anything else raised by the ledger
is a programming error and must abort the run instead of being downgraded to
an order rejection.
"""


class InsufficientCashError(ValueError):
    """A BUY fill would cost more cash than the portfolio holds."""


class InsufficientSharesError(ValueError):
    """A SELL fill exceeds the position or its T+1 sellable quantity."""
