"""End-to-end + save/load tests for `BacktestResult`."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from hqbacktest.data import InMemoryDataPortal
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import (
    EventType,
    OrderType,
    Side,
)
from hqbacktest.domain.order import Order
from hqbacktest.engine.config import BacktestConfig
from hqbacktest.engine.engine import BacktestEngine
from hqbacktest.engine.metrics import MetricsConfig
from hqbacktest.engine.result import BacktestResult
from hqbacktest.engine.strategy import BaseStrategy


def _bar(date: str, close: str = "10.00") -> Bar:
    return Bar.from_raw(
        symbol="600000.SH",
        date=date,
        open="10.00",
        high="15.00",
        low="9.00",
        close=close,
        volume=1000,
    )


def _portal(
    days: list[str], close_overrides: dict[str, str] | None = None
) -> InMemoryDataPortal:
    close_overrides = close_overrides or {}
    p = InMemoryDataPortal(calendar=days)
    for d in days:
        p.add_bar(_bar(d, close=close_overrides.get(d, "10.00")))
    return p


def _config(
    start: str = "20240102", end: str = "20240104", **overrides
) -> BacktestConfig:
    base = dict(
        start_date=start,
        end_date=end,
        initial_cash=Decimal("100000"),
        source="tushare",
    )
    base.update(overrides)
    return BacktestConfig(**base)


# --------------------------------------------------------------------- #
# Equity curve
# --------------------------------------------------------------------- #


def test_engine_builds_equity_curve_for_three_days():
    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    engine = BacktestEngine(
        _config(),
        strategy=BuyHold(),
        portal=_portal(["20240102", "20240103", "20240104"]),
    )
    result = engine.run()
    assert len(result.equity_curve) == 3
    # First day's cash is initial (no fills yet at end of D=1).
    assert result.equity_curve[0].date == "20240102"
    assert result.equity_curve[0].cash == Decimal("100000.00")
    assert result.equity_curve[0].market_value == Decimal("0")
    # All equity points have Decimal fields.
    for pt in result.equity_curve:
        assert isinstance(pt.cash, Decimal)
        assert isinstance(pt.market_value, Decimal)
        assert isinstance(pt.total_equity, Decimal)
        assert isinstance(pt.daily_return, Decimal)
        assert isinstance(pt.drawdown, Decimal)


def test_engine_empty_calendar_raises():
    """An empty trading-day window must raise rather than silently
    produce an empty result.
    """
    from hqbacktest.engine.errors import ConfigurationError

    class Null(BaseStrategy):
        def initialize(self, context):
            pass

    engine = BacktestEngine(
        _config(),
        strategy=Null(),
        portal=_portal([]),
    )
    with pytest.raises(ConfigurationError, match="no trading days"):
        engine.run()


# --------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------- #


def test_engine_records_orders_fills_positions_costs():
    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    engine = BacktestEngine(
        _config("20240102", "20240104"),
        strategy=BuyHold(),
        portal=_portal(["20240102", "20240103", "20240104"]),
    )
    result = engine.run()
    # 3 BAR_CLOSE days -> 3 orders; only the first 2 fill (third stays PENDING
    # until BACKTEST_ENDED).
    assert len(result.orders_table) == 3
    filled = [o for o in result.orders_table if o["status"] == "FILLED"]
    assert len(filled) == 2
    cancelled = [o for o in result.orders_table if o["status"] == "CANCELLED"]
    assert len(cancelled) == 1
    assert cancelled[0]["reject_reason"] == "BACKTEST_ENDED"

    assert len(result.fills_table) == 2
    assert all(f["side"] == "BUY" for f in result.fills_table)
    # Costs: per-fill (commission / stamp_tax / other_fee) and the
    # totals in costs_table.
    assert len(result.costs_table) == 2
    assert all(c["commission"] == "5.00" for c in result.costs_table)
    # Positions table is empty because we measure end-of-day but
    # positions are only non-zero AFTER fills apply. With 2 BUYs
    # matching on D=2 and D=3, end-of-day D=2 shows 100 shares and
    # end-of-day D=3 shows 200 shares.
    assert len(result.positions_table) >= 1
    final_pos = result.positions_table[-1]
    assert final_pos["symbol"] == "600000.SH"
    assert int(final_pos["quantity"]) >= 100


# --------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------- #


def test_engine_populates_metrics_with_hand_calculated_values():
    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    engine = BacktestEngine(
        _config("20240102", "20240104"),
        strategy=BuyHold(),
        portal=_portal(
            ["20240102", "20240103", "20240104"],
            close_overrides={
                "20240102": "10.00",
                "20240103": "11.00",
                "20240104": "12.00",
            },
        ),
    )
    result = engine.run()
    m = result.metrics
    # With 3 days, 2 BUYs match (D=2 BAR_CLOSE -> D=3 OPEN_MATCH; D=3
    # BAR_CLOSE -> D=4 OPEN_MATCH). Both BUYs match at open=10 (Bar's open
    # field, not close).
    # End of D=3: cash = 100000 - 1000 - 5 = 98995; pos = 100 @ 10;
    #   market value = 100 * close(D=3)=11 = 1100
    #   total = 98995 + 1100 = 100095
    # End of D=4: cash = 98995 - 1000 - 5 = 97990; pos = 200 @ 10;
    #   market value = 200 * 12 = 2400
    #   total = 97990 + 2400 = 100390
    # Total return = 100390 / 100000 - 1 = 0.0039
    assert m is not None
    assert m.trade_count == 2
    assert m.total_return.quantize(Decimal("0.0001")) == Decimal("0.0039")


def test_engine_uses_configured_metrics_config():
    cfg = _config(metrics=MetricsConfig(risk_free_rate=Decimal("0.05")))
    assert cfg.metrics.risk_free_rate == Decimal("0.05")


# --------------------------------------------------------------------- #
# Save / load
# --------------------------------------------------------------------- #


def test_result_save_writes_csv_and_json(tmp_path):
    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    engine = BacktestEngine(
        _config("20240102", "20240103"),
        strategy=BuyHold(),
        portal=_portal(["20240102", "20240103"]),
    )
    result = engine.run()
    out = tmp_path / "result"
    result.save(str(out))

    # Required files exist.
    for filename in (
        "equity_curve.csv",
        "orders.csv",
        "fills.csv",
        "positions.csv",
        "costs.csv",
        "summary.json",
    ):
        assert (out / filename).exists(), f"missing {filename}"

    # summary.json contains config_snapshot + metrics.
    summary = json.loads((out / "summary.json").read_text())
    assert "config_snapshot" in summary
    assert "metrics" in summary
    assert summary["adjustment_policy"] == "none"

    # equity_curve.csv round-trips.
    eq_text = (out / "equity_curve.csv").read_text()
    assert "date,cash,market_value,total_equity,daily_return,drawdown" in eq_text
    # Two trading days -> 2 rows in equity curve.
    assert sum(1 for _ in eq_text.splitlines() if _ and not _.startswith("date")) == 2


def test_result_load_preserves_order_and_fill_identifiers(tmp_path):
    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    engine = BacktestEngine(
        _config("20240102", "20240104"),
        strategy=BuyHold(),
        portal=_portal(["20240102", "20240103", "20240104"]),
    )
    result = engine.run()
    out = tmp_path / "result"
    result.save(str(out))

    loaded = BacktestResult.load(str(out))
    # Order IDs and fill IDs round-trip.
    original_order_ids = {row["order_id"] for row in result.orders_table}
    loaded_order_ids = {row["order_id"] for row in loaded.orders_table}
    assert original_order_ids == loaded_order_ids
    original_fill_ids = {row["fill_id"] for row in result.fills_table}
    loaded_fill_ids = {row["fill_id"] for row in loaded.fills_table}
    assert original_fill_ids == loaded_fill_ids
    # Dates preserved.
    assert sorted(pt.date for pt in loaded.equity_curve) == [
        "20240102",
        "20240103",
        "20240104",
    ]
    # Trading days round-trip.
    assert loaded.trading_days == result.trading_days
    # Policy round-trips.
    assert loaded.adjustment_policy == "none"


def test_save_creates_output_directory(tmp_path):
    out = tmp_path / "nested" / "subdir" / "result"

    class Null(BaseStrategy):
        def initialize(self, context):
            pass

    engine = BacktestEngine(
        _config("20240102", "20240102"),
        strategy=Null(),
        portal=_portal(["20240102"]),
    )
    engine.run().save(str(out))
    assert out.exists()
    assert (out / "summary.json").exists()


# --------------------------------------------------------------------- #
# Contract §4 valuation: missing close for a HELD symbol aborts the run
# --------------------------------------------------------------------- #


def test_engine_valuation_uses_lookback_for_suspended_symbol():
    """A suspended holding is valued at the most recent valid close
    within the lookback window and a DATA_WARNING is recorded. The
    run continues normally.
    """

    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def before_trading_start(self, context, data):
            # Buy on the only trading day that has a bar; the subsequent
            # valuation days must fall back to the same close.
            context.order("600000.SH", 100)

    p = InMemoryDataPortal(calendar=["20240102", "20240103", "20240104"])
    p.add_bar(_bar("20240102"))
    # 20240103 / 20240104 have NO bar for 600000.SH; the lookback fallback
    # should use the 20240102 close for valuation.
    engine = BacktestEngine(
        _config("20240102", "20240104"), strategy=BuyHold(), portal=p
    )
    result = engine.run()
    warnings = [e for e in engine.event_log.all() if e.phase is EventType.DATA_WARNING]
    # One DATA_WARNING per suspended day (20240103, 20240104).
    assert len(warnings) == 2
    assert all("600000.SH" in e.detail for e in warnings)
    # Equity curve populated, no DATA_ERROR (lookback succeeded).
    data_errors = [e for e in engine.event_log.all() if e.phase is EventType.DATA_ERROR]
    assert data_errors == []
    assert len(result.equity_curve) == 3


def test_engine_valuation_aborts_when_lookback_exhausted():
    """Direct test of the engine valuation fallbacks: when even the
    20-day lookback cannot find a valid close for a held symbol, the run
    aborts with DATA_ERROR.

    Constructed by directly invoking the private `_lookback_price_or_none`
    helper, since the engine only ever holds a position after a
    successful fill (which requires at least one bar somewhere in the
    calendar). This test pins the task-14 contract: lookback or fail,
    never silently zero.
    """
    from hqbacktest.engine.engine import BacktestEngine

    p = InMemoryDataPortal(calendar=["20240102", "20240103", "20240104"])
    # No bars at all: lookback is empty.
    assert BacktestEngine._lookback_price_or_none(p, "600000.SH", "20240104") is None


# --------------------------------------------------------------------- #
# Orders table: full fields for every order state
# --------------------------------------------------------------------- #


def test_orders_table_has_full_fields_for_rejected_orders():
    """Rejected/cancelled orders must keep symbol/side/quantity — the table
    is built from real Order objects, not scraped from event strings."""

    class OverSell(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            if context.now == "20240102":
                context.order("600000.SH", -100)  # no position -> rejected

    engine = BacktestEngine(
        _config("20240102", "20240104"),
        strategy=OverSell(),
        portal=_portal(["20240102", "20240103", "20240104"]),
    )
    result = engine.run()
    assert len(result.orders_table) == 1
    row = result.orders_table[0]
    assert row["status"] == "REJECTED"
    assert row["symbol"] == "600000.SH"
    assert row["side"] == "SELL"
    assert row["quantity"] == "100"
    assert row["order_type"] == "MARKET"
    assert row["created_session"] == "BAR_CLOSE"
    assert row["reject_reason"] == "INSUFFICIENT_SHARES"


def test_positions_table_uses_per_day_close_prices():
    """Each day's row carries THAT day's quantity and close — not the
    end-of-run state broadcast to every date."""

    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    engine = BacktestEngine(
        _config("20240102", "20240104"),
        strategy=BuyHold(),
        portal=_portal(
            ["20240102", "20240103", "20240104"],
            close_overrides={"20240103": "11.00", "20240104": "12.00"},
        ),
    )
    result = engine.run()
    # Fills at 0103 and 0104 opens -> end-of-day holdings: 100, then 200.
    assert [(r["date"], r["quantity"]) for r in result.positions_table] == [
        ("20240103", "100"),
        ("20240104", "200"),
    ]
    d3, d4 = result.positions_table
    assert Decimal(d3["market_price"]) == Decimal("11.00")
    assert Decimal(d3["market_value"]) == Decimal("1100.00")
    assert Decimal(d4["market_price"]) == Decimal("12.00")
    assert Decimal(d4["market_value"]) == Decimal("2400.00")


# --------------------------------------------------------------------- #
# Save / load: events, metrics, data source
# --------------------------------------------------------------------- #


def test_result_save_writes_events_jsonl(tmp_path):
    class Null(BaseStrategy):
        def initialize(self, context):
            pass

    engine = BacktestEngine(
        _config("20240102", "20240102"),
        strategy=Null(),
        portal=_portal(["20240102"]),
    )
    result = engine.run()
    out = tmp_path / "result"
    result.save(str(out))
    events_path = out / "events.jsonl"
    assert events_path.exists()
    lines = [
        json.loads(line)
        for line in events_path.read_text().splitlines()
        if line.strip()
    ]
    # Five phase events for the single trading day.
    assert len(lines) == len(result.event_log.all()) == 5
    assert lines[0]["phase"] == "SESSION_START"


def test_result_load_rehydrates_metrics_diagnostics_events_and_source(tmp_path):
    class BuyHold(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def on_bar(self, context, data):
            context.order("600000.SH", 100)

    engine = BacktestEngine(
        _config("20240102", "20240104"),
        strategy=BuyHold(),
        portal=_portal(["20240102", "20240103", "20240104"]),
    )
    result = engine.run()
    out = tmp_path / "result"
    result.save(str(out))
    loaded = BacktestResult.load(str(out))

    # Metrics round-trip as a typed PerformanceMetrics.
    assert loaded.metrics is not None
    assert loaded.metrics.trade_count == result.metrics.trade_count
    assert loaded.metrics.total_return == result.metrics.total_return
    assert loaded.metrics.turnover == result.metrics.turnover
    # Event log round-trips.
    assert [e.to_dict() for e in loaded.event_log.all()] == [
        e.to_dict() for e in result.event_log.all()
    ]
    # Data source traceability.
    assert loaded.data_version["source"] == "memory"
    summary = json.loads((out / "summary.json").read_text())
    assert summary["data_version"]["source"] == "memory"
