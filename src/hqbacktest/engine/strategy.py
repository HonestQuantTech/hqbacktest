"""Strategy protocol and base implementation.

Two layers are exposed:

* `Strategy` — the `Protocol` shape used by the engine (task 5, unchanged).
* `BaseStrategy` — a convenience base class that adds explicit lifecycle
  tracking: subclassing overrides the same four methods but the engine
  enforces "must `initialize` before any other callback" and rejects
  post-`run()` usage.

Both layers share the same four callbacks (initialize, before_trading_start,
on_bar, after_trading_end); strategy authors should pick the layer that
matches their ergonomics — Protocol for duck-typed decorators, BaseStrategy
for explicit `super().initialize(context)` patterns.
"""

from typing import Any, Protocol, runtime_checkable

from ..domain.enums import EventType
from .events import EngineEvent


@runtime_checkable
class Strategy(Protocol):
    """Minimum strategy interface used by the engine clock."""

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


class BaseStrategy:
    """Convenience base class with explicit lifecycle tracking.

    Subclasses override the four callbacks. The engine:
        * calls `initialize` exactly once before any other callback;
        * calls `before_trading_start` / `on_bar` / `after_trading_end`
          only after `initialize`;
        * raises `StrategyLifecycleError` if the strategy mis-uses the API
          (e.g. calling `context.order(...)` outside a callback).
    """

    # ------------------------------------------------------------------ #
    # Optional user-facing universe declaration
    # ------------------------------------------------------------------ #

    def initialize(self, context) -> None:
        """Override to set universe and stash initial parameters.

        Default implementation does nothing; subclasses may call
        ``context.set_universe([...])`` (added in task 6) to declare the
        tradeable set. The engine guarantees this is the first method
        invoked in the run.
        """
        return None

    def before_trading_start(self, context, data) -> None:
        """Override to issue orders before the open using yesterday's bar."""
        return None

    def on_bar(self, context, data) -> None:
        """Override to react to today's close and queue orders for tomorrow."""
        return None

    def after_trading_end(self, context) -> None:
        """Override for post-close bookkeeping (no order submission)."""
        return None

    # ------------------------------------------------------------------ #
    # Optional helpers
    # ------------------------------------------------------------------ #

    def log(self, context, message: str) -> None:
        """Append a strategy message to the engine event log."""
        phase = context.phase if context.phase is not None else EventType.SESSION_START
        context.record_event(
            EngineEvent(
                date=context.now,
                phase=phase,
                detail=message,
            )
        )
