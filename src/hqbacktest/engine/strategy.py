"""Strategy protocol and a built-in null implementation.

This module defines the *minimum* contract needed by the event clock in task
5: `initialize`, `before_trading_start`, `on_bar` and `after_trading_end`.
Task 6 widens this with the full `BaseStrategy` class (subclass-friendly,
with explicit lifecycle errors); task 5 only needs the protocol-shaped object.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Strategy(Protocol):
    """Minimum strategy interface used by the engine clock in task 5."""

    def initialize(self, context: Any) -> None: ...

    def before_trading_start(self, context: Any, data: Any) -> None: ...

    def on_bar(self, context: Any, data: Any) -> None: ...

    def after_trading_end(self, context: Any) -> None: ...


class NullStrategy:
    """Strategy that does nothing. Used as the engine's default."""

    def initialize(self, context) -> None:
        return None

    def before_trading_start(self, context, data) -> None:
        return None

    def on_bar(self, context, data) -> None:
        return None

    def after_trading_end(self, context) -> None:
        return None
