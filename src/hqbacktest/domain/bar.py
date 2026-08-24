"""Bar: a single trading day's OHLCV record."""

from dataclasses import dataclass
from decimal import Decimal

from .money import NumberLike, quantize_price


@dataclass(frozen=True)
class Bar:
    """A single trading day's bar for a single symbol.

    All prices are unadjusted (contract §3.1). OHLC and volume are
    immutable.

    `volume` is the day's traded volume expressed in **lots (手)** — the
    unit used by the upstream Tushare `hqdata` adapter. 1 lot = 100
    shares for both Shanghai and Shenzhen. Callers that need shares must
    multiply by `LOT_SIZE` (see `domain.money`). Strategies that compare
    volumes across symbols MUST do so on this same lot basis.
    """

    symbol: str
    date: str  # YYYYMMDD
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int  # 单位：手 (1 手 = 100 股)

    def __post_init__(self) -> None:
        if not self.symbol or not isinstance(self.symbol, str):
            raise ValueError(f"symbol must be a non-empty string, got {self.symbol!r}")
        if len(self.date) != 8 or not self.date.isdigit():
            raise ValueError(f"date must be YYYYMMDD, got {self.date!r}")
        if self.volume < 0:
            raise ValueError(f"volume must be non-negative, got {self.volume}")
        for field_name in ("open", "high", "low", "close"):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                raise ValueError(
                    f"{field_name} must be Decimal, got {type(value).__name__}"
                )
            if value <= 0:
                raise ValueError(f"{field_name} must be positive, got {value}")
        if self.low > self.high:
            raise ValueError(f"low ({self.low}) cannot exceed high ({self.high})")
        if self.open < self.low or self.open > self.high:
            raise ValueError("open must be within [low, high]")
        if self.close < self.low or self.close > self.high:
            raise ValueError("close must be within [low, high]")

    @classmethod
    def from_raw(
        cls,
        symbol: str,
        date: str,
        open: NumberLike,
        high: NumberLike,
        low: NumberLike,
        close: NumberLike,
        volume: int,
    ) -> "Bar":
        """Construct a Bar while quantizing prices to 4 decimals."""
        return cls(
            symbol=symbol,
            date=date,
            open=quantize_price(open),
            high=quantize_price(high),
            low=quantize_price(low),
            close=quantize_price(close),
            volume=int(volume),
        )
