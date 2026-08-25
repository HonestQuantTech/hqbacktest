"""Helper strategy modules for the CLI tests.

These are intentionally separate from the public example modules so
`resolve_strategy` exercises kwargs plumbing without affecting the
end-to-end buy-and-hold / moving-average regression tests.
"""

from hqbacktest.engine.strategy import BaseStrategy


class KwargReceivingStrategy(BaseStrategy):
    """Trivial strategy used to verify the `[strategy].kwargs` plumbing."""

    def __init__(self, answer: int = 0) -> None:
        self.answer = answer

    def initialize(self, context) -> None:
        # No-op.
        return None

    def received(self):
        return {"answer": self.answer}


class NotAStrategy:
    """Plain class (NOT a BaseStrategy subclass).

    `resolve_strategy` must reject this if the user does not specify
    `class_name` and the module exposes no BaseStrategy subclass.
    """

    def __init__(self) -> None:
        pass
