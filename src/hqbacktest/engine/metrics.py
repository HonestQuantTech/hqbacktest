"""Performance metrics (task 10).

Formulas (all documented in README and contract doc):
    * `total_return`            = (final_equity / initial_equity) - 1
    * `daily_return`            = equity[t] / equity[t-1] - 1   (simple)
    * `annualized_return`       = (1 + total_return) ** (trading_days /
                                  annual_trading_days) - 1
    * `daily_volatility`        = stdev(daily_returns)  (sample stddev; ddof=1)
    * `annualized_volatility`   = daily_volatility * sqrt(annual_trading_days)
    * `sharpe_ratio`            = (annualized_return - risk_free_rate) /
                                  annualized_volatility
    * `max_drawdown`            = max(peak - current) / peak over the
                                  whole equity curve
    * `turnover`                = (sum(buy value) + sum(sell value)) / 2 /
                                  initial_equity   (one-sided average)
    * `trade_count`             = number of `Fill` records
    * `win_rate`                = SELL fills above their running average
                                  cost / total SELL fills; `None` if no
                                  SELL fills

Edge cases (per task 10 verification "空回测 / 单日 / 零波动 / 全亏损 / 无交易"):
    * `len(equity_curve) == 0` (empty run): all metrics 0 or `None`; notes
      record "no trading days".
    * `len(equity_curve) == 1` (single day): `total_return` is the only
      computable ratio; annualised / vol / sharpe are `None` and a note
      is added.
    * `daily_volatility == 0` (flat equity): `sharpe_ratio` is `None`; a
      note records the reason.
    * `no SELL fills`: `win_rate` is `None`.
    * `no fills at all`: `trade_count == 0`, `turnover == 0`,
      `win_rate is None`.

All formulas take a `MetricsConfig` so the risk-free rate and trading-day
count are traceable to the run, not hidden in code.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from math import sqrt
from statistics import StatisticsError, stdev
from typing import List, Optional, Sequence, Tuple

from ..domain.fill import Fill
from ..domain.enums import Side


@dataclass(frozen=True)
class MetricsConfig:
    """Traceable metrics parameters.

    `risk_free_rate` is the *annualised* risk-free rate (e.g.
    `Decimal("0.025")` for 2.5% per year). `annual_trading_days` is
    the count used to annualise returns / volatility; A-share default
    is 252.
    """

    risk_free_rate: Decimal = Decimal("0.0")
    annual_trading_days: int = 252

    def __post_init__(self) -> None:
        if not isinstance(self.risk_free_rate, Decimal):
            raise TypeError(
                f"risk_free_rate must be Decimal, got "
                f"{type(self.risk_free_rate).__name__}"
            )
        if not isinstance(self.annual_trading_days, int):
            raise TypeError(
                f"annual_trading_days must be int, got "
                f"{type(self.annual_trading_days).__name__}"
            )
        if self.annual_trading_days <= 0:
            raise ValueError("annual_trading_days must be positive")


@dataclass(frozen=True)
class EquityPoint:
    """One row of the daily equity curve."""

    date: str  # YYYYMMDD
    cash: Decimal
    market_value: Decimal
    total_equity: Decimal
    daily_return: Decimal
    drawdown: Decimal


@dataclass(frozen=True)
class PerformanceMetrics:
    """All v0.1 metrics. `None` indicates an undefined value
    (e.g. `sharpe_ratio` for a flat equity curve)."""

    total_return: Decimal
    annualized_return: Optional[Decimal]
    daily_volatility: Optional[Decimal]
    annualized_volatility: Optional[Decimal]
    sharpe_ratio: Optional[Decimal]
    max_drawdown: Decimal
    turnover: Decimal
    trade_count: int
    win_rate: Optional[Decimal]
    notes: Tuple[str, ...] = ()


def _daily_returns(equity: Sequence[Decimal]) -> List[Decimal]:
    """`equity[t] / equity[t-1] - 1` for t >= 1. Returns 0 for t=0."""
    out: List[Decimal] = [Decimal("0")]
    for prev, curr in zip(equity[:-1], equity[1:]):
        if prev == 0:
            out.append(Decimal("0"))
        else:
            out.append(curr / prev - Decimal("1"))
    return out


def _drawdown_series(returns: Sequence[Decimal]) -> List[Decimal]:
    """Per-step drawdown (>= 0) given the cumulative return series."""
    peaks: List[Decimal] = [Decimal("1")]
    drawdowns: List[Decimal] = [Decimal("0")]
    cum = Decimal("1")
    for r in returns:
        cum = cum * (Decimal("1") + r)
        peak = max(peaks[-1], cum)
        peaks.append(peak)
        if peak == 0:
            drawdowns.append(Decimal("0"))
        else:
            drawdowns.append(max(Decimal("0"), (peak - cum) / peak))
    # Drop the seed element; we want one drawdown per return.
    return drawdowns[1:]


def compute_metrics(
    equity_curve: Sequence[EquityPoint],
    fills: Sequence[Fill],
    initial_cash: Decimal,
    config: MetricsConfig,
) -> PerformanceMetrics:
    """Compute all v0.1 metrics from an equity curve and the fill list."""
    notes: List[str] = []
    n_days = len(equity_curve)
    final_equity = equity_curve[-1].total_equity if n_days else initial_cash
    total_return = (
        (final_equity / initial_cash - Decimal("1"))
        if initial_cash > 0
        else Decimal("0")
    )

    if n_days == 0:
        notes.append("no trading days")

    # Annualised return: (1 + total_return) ** (n / annual) - 1.
    if n_days <= 1 or initial_cash <= 0:
        annualized_return: Optional[Decimal] = None
        if n_days <= 1:
            notes.append("annualized_return: requires > 1 trading day")
    else:
        growth = Decimal("1") + total_return
        if growth <= 0:
            annualized_return = None
            notes.append("annualized_return: total return <= -100%")
        else:
            # Use float for the power operation; cast back to Decimal.
            exponent = Decimal(n_days) / Decimal(config.annual_trading_days)
            annualized_return = Decimal(float(growth) ** float(exponent)) - Decimal("1")

    # Daily volatility (per-day stdev) and its annualisation; Sharpe uses
    # the annualised pair so the units match.
    returns = _daily_returns([pt.total_equity for pt in equity_curve])
    annualized_volatility: Optional[Decimal]
    if n_days < 2:
        daily_volatility = None
        annualized_volatility = None
        sharpe_ratio = None
        notes.append("daily_volatility: requires >= 2 daily returns")
    else:
        try:
            vol_per_day = Decimal(str(stdev([float(r) for r in returns[1:]])))
        except StatisticsError:
            vol_per_day = Decimal("0")
        daily_volatility = vol_per_day
        if vol_per_day == 0:
            annualized_volatility = Decimal("0")
            sharpe_ratio = None
            notes.append("sharpe_ratio: undefined (zero volatility)")
        else:
            annualized_volatility = vol_per_day * Decimal(
                str(sqrt(config.annual_trading_days))
            )
            if annualized_return is None:
                sharpe_ratio = None
            else:
                excess = annualized_return - config.risk_free_rate
                sharpe_ratio = excess / annualized_volatility

    # Max drawdown from the equity curve itself (avoids precision drift).
    max_dd = max((pt.drawdown for pt in equity_curve), default=Decimal("0"))

    # Turnover, trade count, win rate. Fills are replayed in CHRONOLOGICAL
    # order in a single pass: each BUY adds to the running basis, each SELL
    # is judged against the average cost at that moment (buys that happen
    # after a sell must not affect its win/loss status).
    buy_value = Decimal("0")
    sell_value = Decimal("0")
    bases: dict[str, list] = {}  # symbol -> [quantity, total_cost]
    sell_count = 0
    wins = 0
    for f in fills:
        if f.side is Side.BUY:
            buy_value += f.amount
            slot = bases.setdefault(f.symbol, [0, Decimal("0")])
            slot[0] += f.quantity
            slot[1] += f.quantity * f.price
        else:
            sell_value += -f.amount  # amount is negative for SELL
            sell_count += 1
            slot = bases.get(f.symbol)
            if slot is None or slot[0] == 0:
                continue  # no basis: counts towards total sells, never a win
            avg_cost = slot[1] / slot[0]
            if f.price > avg_cost:
                wins += 1
            consumed = min(slot[0], f.quantity)
            slot[0] -= consumed
            slot[1] -= avg_cost * consumed
    turnover = (
        (buy_value + sell_value) / Decimal(2) / initial_cash
        if initial_cash > 0
        else Decimal("0")
    )
    if sell_count:
        win_rate = Decimal(wins) / Decimal(sell_count)
    else:
        win_rate = None
        notes.append("win_rate: no SELL fills")

    if not fills:
        notes.append("trade_count: no fills")

    return PerformanceMetrics(
        total_return=total_return,
        annualized_return=annualized_return,
        daily_volatility=daily_volatility,
        annualized_volatility=annualized_volatility,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_dd,
        turnover=turnover,
        trade_count=len(fills),
        win_rate=win_rate,
        notes=tuple(notes),
    )
