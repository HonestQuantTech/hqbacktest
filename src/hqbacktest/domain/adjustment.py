"""CorporateActionAdjustment: v0.1 placeholder.

With AdjustmentPolicy=none in v0.1, this dataclass exists to keep the call site
typed and reserved for the future `CorporateActionProvider` (task 9+). All
adjustments produced in v0.1 are no-ops and must NOT modify cash, holdings,
sellable quantity, cost basis or equity (contract §6 rule 8).
"""

from dataclasses import dataclass
from decimal import Decimal

from .money import quantize_cash


@dataclass(frozen=True)
class CorporateActionAdjustment:
    """Description of a single corporate action at its ex-date.

    `factor_ratio` is the multiplier the price underwent (1.0 means no change).
    `cash_per_share` is the per-share dividend in CNY (0 for splits, etc.).
    """

    symbol: str
    ex_date: str  # YYYYMMDD
    action_type: str  # "DIVIDEND" | "SPLIT" | "RIGHTS" | "MERGE" | ...
    factor_ratio: Decimal = Decimal(1)
    cash_per_share: Decimal = Decimal(0)
    note: str = ""

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if len(self.ex_date) != 8 or not self.ex_date.isdigit():
            raise ValueError(f"ex_date must be YYYYMMDD, got {self.ex_date!r}")
        if not self.action_type:
            raise ValueError("action_type must be non-empty")
        if not isinstance(self.factor_ratio, Decimal) or self.factor_ratio <= 0:
            raise ValueError(
                f"factor_ratio must be positive Decimal, got {self.factor_ratio!r}"
            )
        if not isinstance(self.cash_per_share, Decimal):
            raise ValueError(
                f"cash_per_share must be Decimal, got {type(self.cash_per_share).__name__}"
            )

    def is_noop(self) -> bool:
        """True iff applying this adjustment changes nothing."""
        return self.factor_ratio == 1 and self.cash_per_share == 0

    @classmethod
    def noop(cls, symbol: str, ex_date: str) -> "CorporateActionAdjustment":
        """Build the canonical no-op adjustment for v0.1."""
        return cls(
            symbol=symbol,
            ex_date=ex_date,
            action_type="NONE",
            factor_ratio=Decimal(1),
            cash_per_share=quantize_cash(0),
            note="v0.1 AdjustmentPolicy=none",
        )
