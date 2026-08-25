"""Corporate-action abstractions.

v0.1 status:
    * `AdjustmentPolicy` is exposed in `domain.enums` but only `NONE` is
      accepted by the engine. Other values are rejected at config
      validation time with a clear reason.
    * `CorporateActionProvider` is a Protocol draft only. The engine
      does not depend on it in v0.1; the field list below documents
      what an implementation MUST provide before any accounting entry
      is written.
    * `FactorDiagnostic` records cross-source / missing / non-positive /
      abnormal-jump observations; the engine emits a single "policy=none"
      marker at run start. The diagnostic collection becomes active
      when `factor_total_return` is enabled (not in v0.1).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Literal, Optional, Protocol, Sequence, Tuple, runtime_checkable

# Canonical v0.1 policy. Any other value must be rejected at config time.
# This is the single definition; `config.py` and `result.py` import it.
V01_ADJUSTMENT_POLICY: str = "none"

# Required fields for any FUTURE `CorporateActionProvider` implementation.
# Listed here so the engine can fail early when an implementation
# doesn't expose them, and so reviewers can audit the contract.
REQUIRED_CORPORATE_ACTION_FIELDS: tuple[str, ...] = (
    "symbol",
    "ex_date",
    "cash_dividend_per_share",
    "stock_dividend_ratio",
    "rights_ratio",
    "rights_price",
    "tax_rate",
    "conversion_ratio",
    "fractional_share_handling",
    "note",
)

# Admission criteria for enabling `factor_total_return`:
# EACH ledger aspect below must have (a) a written accounting semantic
# and (b) a hand-computable regression test BEFORE the policy is
# accepted by `BacktestConfig`. Until every entry is satisfied, the
# policy stays rejected at config validation time.
FACTOR_TOTAL_RETURN_ADMISSION_CRITERIA: tuple[str, ...] = (
    "cash",  # how dividends/splits change cash without fabricating payouts
    "position_quantity",  # how share counts change on splits/bonus issues
    "sellable_quantity",  # how T+1 sellable interacts with quantity changes
    "avg_cost",  # how cost basis is restated on ex-dates
    "fills_and_sells",  # how fills straddling an ex-date are priced/booked
    "day_end_valuation",  # how daily market value is computed across ex-dates
    "result_metrics",  # how returns/metrics are defined under adjustment
)


FractionalShareHandling = Literal["CASH", "ROUND", "REJECT"]


# --------------------------------------------------------------------- #
# CorporateAction (design draft, NOT IMPLEMENTED in v0.1)
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class CorporateAction:
    """One corporate action at its ex-date.

    Every field here MUST be populated (with `Decimal(0)` for "not
    applicable") before any accounting entry is written by a future
    implementation. Hqbacktest v0.1 never instantiates this with
    non-zero values.
    """

    symbol: str
    ex_date: str  # YYYYMMDD
    cash_dividend_per_share: Decimal = Decimal(0)
    stock_dividend_ratio: Decimal = Decimal(0)
    rights_ratio: Decimal = Decimal(0)
    rights_price: Decimal = Decimal(0)
    tax_rate: Decimal = Decimal(0)
    conversion_ratio: Decimal = Decimal(0)
    fractional_share_handling: FractionalShareHandling = "CASH"
    note: str = ""

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if len(self.ex_date) != 8 or not self.ex_date.isdigit():
            raise ValueError(f"ex_date must be YYYYMMDD, got {self.ex_date!r}")
        for field_name in (
            "cash_dividend_per_share",
            "stock_dividend_ratio",
            "rights_ratio",
            "rights_price",
            "tax_rate",
            "conversion_ratio",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                raise ValueError(
                    f"{field_name} must be Decimal, got {type(value).__name__}"
                )
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative, got {value}")
        if self.fractional_share_handling not in ("CASH", "ROUND", "REJECT"):
            raise ValueError(
                f"fractional_share_handling must be CASH/ROUND/REJECT, "
                f"got {self.fractional_share_handling!r}"
            )


# --------------------------------------------------------------------- #
# CorporateActionProvider (Protocol — design draft only)
# --------------------------------------------------------------------- #


@runtime_checkable
class CorporateActionProvider(Protocol):
    """Future interface for retrieving authoritative corporate actions.

    Required by future tasks before any accounting entry is written. The
    protocol is NOT implemented in v0.1; the engine does not depend on
    it. The list of required attributes matches
    `REQUIRED_CORPORATE_ACTION_FIELDS` plus `actions_for`.
    """

    def actions_for(
        self, symbol: str, start: str, end: str
    ) -> List[CorporateAction]: ...


# --------------------------------------------------------------------- #
# Factor diagnostics (active when AdjustmentPolicy != "none")
# --------------------------------------------------------------------- #

#: All diagnostic kinds the analyzer can emit.
DIAGNOSTIC_KINDS: tuple[str, ...] = (
    "missing",
    "non_positive",
    "cross_source",
    "abnormal_jump",
)

#: Default acceptable band for the ratio between consecutive factors of the
#: SAME source. A ratio outside [0.5, 2.0] means the factor doubled or
#: halved overnight — almost always a data problem, not a corporate action.
DEFAULT_JUMP_BAND: Tuple[Decimal, Decimal] = (Decimal("0.5"), Decimal("2.0"))

#: Tolerance for cross-source factor comparison (relative difference).
DEFAULT_CROSS_SOURCE_TOLERANCE = Decimal("0.0001")


@dataclass(frozen=True)
class FactorDiagnostic:
    """One observation about factor quality.

    `kind` is one of `DIAGNOSTIC_KINDS`:
        * "missing"           - no factor for this (symbol, date).
        * "non_positive"      - factor <= 0.
        * "cross_source"      - factor disagrees across sources.
        * "abnormal_jump"     - daily ratio outside expected band.
    """

    symbol: str
    date: str
    kind: str
    detail: str

    def __post_init__(self) -> None:
        if self.kind not in DIAGNOSTIC_KINDS:
            raise ValueError(f"unknown diagnostic kind: {self.kind!r}")


def analyze_factor_series(
    symbol: str,
    expected_dates: Sequence[str],
    factors: Sequence[Tuple[str, Decimal]],
    *,
    reference: Optional[Sequence[Tuple[str, Decimal]]] = None,
    jump_band: Tuple[Decimal, Decimal] = DEFAULT_JUMP_BAND,
    cross_source_tolerance: Decimal = DEFAULT_CROSS_SOURCE_TOLERANCE,
) -> List[FactorDiagnostic]:
    """Same-source factor-quality diagnostics. Pure function.

    Reads a factor series and returns one `FactorDiagnostic` per anomaly:
        * "missing"       - an expected trading day has no factor row;
        * "non_positive"  - a factor is zero or negative;
        * "abnormal_jump" - the ratio of consecutive factors leaves
                            `jump_band` (same source only; factor absolute
                            values are NEVER compared across sources);
        * "cross_source"  - `reference` (another source's series) disagrees
                            on the same date beyond `cross_source_tolerance`.

    The function never touches cash, positions or any ledger state; it only
    reads the two series and returns observations.
    """
    by_date = {date: value for date, value in factors}
    diagnostics: List[FactorDiagnostic] = []

    for date in expected_dates:
        if date not in by_date:
            diagnostics.append(
                FactorDiagnostic(
                    symbol=symbol,
                    date=date,
                    kind="missing",
                    detail="no factor row for an expected trading day",
                )
            )

    ordered = sorted(by_date.items())  # ascending by date string
    previous: Optional[Tuple[str, Decimal]] = None
    for date, value in ordered:
        if value <= 0:
            diagnostics.append(
                FactorDiagnostic(
                    symbol=symbol,
                    date=date,
                    kind="non_positive",
                    detail=f"factor={value}",
                )
            )
        if previous is not None and previous[1] > 0 and value > 0:
            ratio = value / previous[1]
            low, high = jump_band
            if ratio < low or ratio > high:
                diagnostics.append(
                    FactorDiagnostic(
                        symbol=symbol,
                        date=date,
                        kind="abnormal_jump",
                        detail=(
                            f"factor ratio {ratio} vs previous day "
                            f"({previous[0]}) outside [{low}, {high}]"
                        ),
                    )
                )
        previous = (date, value)

    if reference is not None:
        ref_by_date = {date: value for date, value in reference}
        for date, value in ordered:
            ref = ref_by_date.get(date)
            if ref is None or ref == 0:
                continue
            if abs(value - ref) / abs(ref) > cross_source_tolerance:
                diagnostics.append(
                    FactorDiagnostic(
                        symbol=symbol,
                        date=date,
                        kind="cross_source",
                        detail=(
                            f"factor {value} disagrees with reference {ref} "
                            f"(tolerance {cross_source_tolerance})"
                        ),
                    )
                )

    diagnostics.sort(key=lambda d: (d.date, d.kind))
    return diagnostics


class FactorDiagnosticCollector:
    """Append-only collector for `FactorDiagnostic` records.

    v0.1 keeps this dormant (the engine records a single "policy=none"
    marker; no factor data is read). When `factor_total_return` is
    enabled in a future task, this collector becomes the audit-trail
    destination for cross-source / missing / non-positive / abnormal-
    jump observations and the engine surfaces them in `BacktestResult`.
    """

    def __init__(self) -> None:
        self._items: List[FactorDiagnostic] = []

    def record(self, diagnostic: FactorDiagnostic) -> None:
        if not isinstance(diagnostic, FactorDiagnostic):
            raise TypeError(
                f"expected FactorDiagnostic, got {type(diagnostic).__name__}"
            )
        self._items.append(diagnostic)

    def all(self) -> List[FactorDiagnostic]:
        return list(self._items)

    def has_any(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)
