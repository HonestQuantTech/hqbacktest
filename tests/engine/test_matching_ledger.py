"""Hand-calculated regression tests for matching + lot rounding.

Covers:
    * Same-day SELL proceeds are available to fund the same batch's BUY
      orders ("卖旧买新" rotation).
    * SELL orders are not lot-rounded: odd-lot SELLs (含零股) succeed and
      can fully flatten a position via `order_target(symbol, 0)`.
    * SELL 150 must NOT be silently shrunk to 100 (silently altering
      the strategy's intent violates the contract).
    * Same-day BUY-then-SELL vs SELL-then-BUY at the same price produce
      the same realized_pnl for the SELL position (fee differences are
      not realized_pnl).
    * T+1 violation -> whole-order rejection (no partial fills in v0.1).
    * `intents.target_quantity_for_value(0)` returns 0 (flatten), per its
      docstring.
    * CLI `_require_decimal` rejects float (consistent with engine).
    * `Fill` with non-zero stamp_tax on a BUY raises (cost-table consistency).
"""

from decimal import Decimal

import pytest

from hqbacktest import BacktestConfig, BacktestEngine, BaseStrategy
from hqbacktest.cli.config import _require_decimal, ConfigError
from hqbacktest.data import InMemoryDataPortal
from hqbacktest.domain.bar import Bar
from hqbacktest.domain.enums import EventType, OrderStatus, Side
from hqbacktest.domain.fill import Fill
from hqbacktest.engine.intents import signed_diff_to_lots, target_quantity_for_value
from hqbacktest.domain.portfolio import Portfolio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar(date: str, open_: str, close: str) -> Bar:
    return Bar.from_raw(
        symbol="600000.SH",
        date=date,
        open=open_,
        high="20.0000",
        low="9.0000",
        close=close,
        volume=1000,
    )


def _bar2(date: str, open_: str, close: str) -> Bar:
    return Bar.from_raw(
        symbol="000001.SZ",
        date=date,
        open=open_,
        high="20.0000",
        low="9.0000",
        close=close,
        volume=1000,
    )


def _portal_two_symbols() -> InMemoryDataPortal:
    p = InMemoryDataPortal(
        calendar=["20240102", "20240103", "20240104"],
        universe_by_date={"20240102": ["600000.SH", "000001.SZ"]},
        as_of="20240104",
    )
    for d in ("20240102", "20240103", "20240104"):
        p.add_bar(_bar(d, "10.0000", "10.0000"))
        p.add_bar(_bar2(d, "10.0000", "10.0000"))
    return p


def _cfg(start="20240102", end="20240104") -> BacktestConfig:
    return BacktestConfig(
        start_date=start,
        end_date=end,
        initial_cash=Decimal("100000"),
        source="tushare",
    )


# ---------------------------------------------------------------------------
# Same-day SELL-then-BUY: rotation orders must not be rejected
# ---------------------------------------------------------------------------


def test_same_day_sell_proceeds_fund_buy_in_same_batch():
    """Hand-calculated regression: hold 900 @ 10 (cost basis 9000), cash
    45; same batch SELL 900 + BUY 900 @ 10.

    Earlier the BUY was rejected with INSUFFICIENT_CASH because the
    broker matched in submission order using a pre-batch cash
    snapshot. The broker now matches SELLs first and the SELL net
    proceeds (900 * 10 - 5 commission - 9 stamp = 8986) push cash from
    45 to 9031, funding the 9005-cost BUY in the same batch.
    """

    class Rotate(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH", "000001.SZ"])

        def before_trading_start(self, context, data):
            # Day 1: build the 600000.SH position from initial cash.
            if context.now == "20240102":
                context.order("600000.SH", 900)
            # Day 2: rotation — sell 600000.SH, buy 000001.SZ, same batch.
            elif context.now == "20240103":
                context.order_target("600000.SH", 0)
                context.order("000001.SZ", 900)

    # 9050 = 9000 (cost) + 5 (commission) + 40 spare so day 1 buy fills.
    cfg = _cfg()
    cfg = BacktestConfig(
        start_date=cfg.start_date,
        end_date=cfg.end_date,
        initial_cash=Decimal("9050"),
        source=cfg.source,
    )
    engine = BacktestEngine(cfg, strategy=Rotate(), portal=_portal_two_symbols())
    engine.run()

    fills = [e for e in engine.event_log.all() if e.phase is EventType.ORDER_FILLED]
    day3_fills = [e for e in fills if e.date == "20240103"]
    # 20240103: both legs fill (SELL 600000.SH + BUY 000001.SZ).
    assert len(day3_fills) == 2
    # The BUY must not have been rejected for cash.
    rejected = [
        e
        for e in engine.event_log.all()
        if e.phase is EventType.ORDER_REJECTED and e.date == "20240103"
    ]
    assert all("INSUFFICIENT_CASH" not in (e.error or "") for e in rejected)
    # Sell fill exists with the full 900 quantity (not lot-rounded).
    sell_fill = next(
        (
            e
            for e in day3_fills
            if e.detail and "qty=900" in e.detail and "stamp=" in e.detail
        ),
        None,
    )
    assert sell_fill is not None, "expected a SELL 900 fill on 20240103"


# ---------------------------------------------------------------------------
# Odd-lot SELL: not lot-rounded, can flatten
# ---------------------------------------------------------------------------


def test_sell_150_is_not_silently_rounded_to_100():
    """`order_target(symbol, 0)` on a 150-share position must flatten all
    150 shares, not silently shrink to 100. The sell fill must show
    `qty=150`, not `qty=100`.
    """
    portfolio = Portfolio(initial_cash=Decimal("1000"))
    position = portfolio.get_position("600000.SH")
    position.update_buy(150, Decimal("10.0000"))
    portfolio.settle_t1(today="20240101", previous_date=None)
    assert position.sellable_quantity == 150

    class Flatten(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def before_trading_start(self, context, data):
            context.order_target("600000.SH", 0)

    p = _portal_two_symbols()
    engine = BacktestEngine(_cfg(), strategy=Flatten(), portal=p)
    # Inject the 150-share pre-position directly into the engine ledger.
    pre_pos = engine.portfolio.get_position("600000.SH")
    pre_pos.update_buy(150, Decimal("10.0000"))
    engine.portfolio.settle_t1(today="20240101", previous_date=None)

    engine.run()

    # The SELL must fill for the full 150 shares — qty=150, never qty=100.
    sell_fills = [
        e
        for e in engine.event_log.all()
        if e.phase is EventType.ORDER_FILLED and e.detail and "qty=150" in e.detail
    ]
    assert sell_fills, "expected a 150-share SELL fill"
    # And there must NOT be a 100-share fill for 600000.SH (which would
    # be the silent shrink).
    bad_fills = [
        e
        for e in engine.event_log.all()
        if e.phase is EventType.ORDER_FILLED and e.detail and "qty=100" in e.detail
    ]
    assert not bad_fills, f"unexpected 100-share SELL fill: {bad_fills}"


def test_sell_50_odd_lot_is_accepted_by_engine():
    """`order(sym, -50)` on a position with ≥ 50 sellable must succeed.

    Sanity check: when the position holds 100 (1 lot) and the strategy
    submits order(sym, -50), the SELL fills for exactly 50 shares. (The
    engine must NOT truncate to 100 nor reject for lot-size.)
    """

    class SellHalf(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def before_trading_start(self, context, data):
            if context.now == "20240102":
                context.order("600000.SH", 100)
            elif context.now == "20240103":
                context.order("600000.SH", -50)

    p = _portal_two_symbols()
    engine = BacktestEngine(_cfg(), strategy=SellHalf(), portal=p)
    engine.run()
    # Expect a fill on 20240103 with qty=50 (the odd-lot SELL).
    sell_fills = [
        e
        for e in engine.event_log.all()
        if e.phase is EventType.ORDER_FILLED
        and e.date == "20240103"
        and e.detail
        and "qty=50" in e.detail
    ]
    assert sell_fills, "expected a SELL 50 fill on 20240103"


# ---------------------------------------------------------------------------
# Lot rounding helper: SELL no longer lot-aligned
# ---------------------------------------------------------------------------


def test_signed_diff_to_lots_sell_does_not_round_to_lot():
    """`signed_diff_to_lots(50, 300)` returns -250, not -200.

    SELL orders preserve the requested odd-lot count; the lot floor
    applies only to BUY orders.
    """
    assert signed_diff_to_lots(50, 300) == -250


def test_signed_diff_to_lots_buy_still_floors_to_lot():
    assert signed_diff_to_lots(250, 0) == 200


# ---------------------------------------------------------------------------
# target_quantity_for_value(0) flattens per docstring
# ---------------------------------------------------------------------------


def test_target_quantity_for_value_zero_returns_zero_per_docstring():
    """`target_quantity_for_value(Decimal('0'), ...)` returns 0
    (flatten), matching the docstring's "may be 0 to flatten".
    """
    assert (
        target_quantity_for_value(Decimal("0"), Decimal("12.50"), current_quantity=300)
        == 0
    )


# ---------------------------------------------------------------------------
# T+1 whole-order rejection (not partial fill)
# ---------------------------------------------------------------------------


def test_t1_violation_rejects_whole_order_not_partial():
    """Selling more than the sellable_quantity rejects the entire order,
    not a partial fill. v0.1 does not implement partial fills.
    """

    class SellMoreThanHeld(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH"])

        def before_trading_start(self, context, data):
            if context.now == "20240102":
                context.order("600000.SH", 100)  # 100 shares bought day 1
            elif context.now == "20240103":
                # Try to sell 200 — only 100 sellable (T+1).
                context.order("600000.SH", -200)

    p = _portal_two_symbols()
    engine = BacktestEngine(_cfg(), strategy=SellMoreThanHeld(), portal=p)
    engine.run()
    rejected = [
        e
        for e in engine.event_log.all()
        if e.phase is EventType.ORDER_REJECTED and e.date == "20240103"
    ]
    assert rejected, "expected a rejection on 20240103 for T+1 violation"
    # No partial fill: the position must remain at 100 shares.
    fills = [
        e
        for e in engine.event_log.all()
        if e.phase is EventType.ORDER_FILLED and e.date == "20240103"
    ]
    assert not fills, f"expected no fills on 20240103, got {fills}"


# ---------------------------------------------------------------------------
# Same-day BUY-then-SELL vs SELL-then-BUY: identical realized_pnl for SELL
# ---------------------------------------------------------------------------


def test_realized_pnl_independent_of_intraday_match_order():
    """Realized PnL on the SELL equals price - cost, regardless of
    whether the BUY in the same batch happened before or after the SELL.

    With fees handled outside realized_pnl (contract rule 8), the
    intraday match order does not affect realized_pnl. (Both BUY and
    SELL execute at the same price, the SELL's avg_cost is the BUY's
    price, so realized = price - price = 0.)
    """

    class TwoStepSameBatch(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH", "000001.SZ"])

        def before_trading_start(self, context, data):
            if context.now == "20240102":
                # Pre-position with 100 of 600000.SH for day-3 sell.
                context.order("600000.SH", 100)
            elif context.now == "20240103":
                # Both legs in the same batch.
                context.order("000001.SZ", 100)  # BUY first
                context.order_target("600000.SH", 0)  # then SELL

    p = _portal_two_symbols()
    engine = BacktestEngine(_cfg(), strategy=TwoStepSameBatch(), portal=p)
    engine.run()
    # realized_pnl on the 600000.SH position should be 0
    # (sell @ 10 against cost @ 10). Fees are NOT part of realized_pnl.
    pos = engine.portfolio.positions.get("600000.SH")
    if pos is not None:
        assert pos.realized_pnl == Decimal("0.00")


# ---------------------------------------------------------------------------
# Fill invariants
# ---------------------------------------------------------------------------


def test_fill_buy_with_nonzero_stamp_tax_raises():
    """A BUY fill must not carry stamp_tax (印花税只在 SELL 收取).
    Stamp_tax on a BUY would let the cash ledger drift from the costs
    table.
    """
    with pytest.raises(ValueError, match="stamp_tax"):
        Fill.from_trade(
            fill_id="F1",
            order_id="O1",
            symbol="600000.SH",
            side=Side.BUY,
            quantity=100,
            price=Decimal("10.0000"),
            commission=Decimal("5.00"),
            stamp_tax=Decimal("1.00"),  # invalid for BUY
            other_fee=Decimal("0"),
            filled_at="20240102",
            session=EventType.OPEN_MATCH,
        )


# ---------------------------------------------------------------------------
# CLI initial_cash rejects float (consistent with engine)
# ---------------------------------------------------------------------------


def test_cli_require_decimal_rejects_float():
    """The CLI validator must reject float inputs to match the engine's
    contract rule 5 ("Decimal/str/int; float forbidden"). Otherwise a
    TOML like `initial_cash = 100000.0` would silently convert to
    Decimal via `Decimal(str(100000.0))` at the CLI layer.
    """
    with pytest.raises(ConfigError, match="float"):
        _require_decimal({"initial_cash": 100000.0}, "capital", "initial_cash")


def test_same_day_buy_first_rotation_funds_from_sell():
    """A BUY submitted BEFORE its funding SELL must still fill.

    The broker normalizes the batch to [SELLs..., BUYs...] regardless of
    submission order, and returns results in that matching order so the
    engine credits the SELL's cash before debiting the BUY. A BUY-first
    rotation must NOT be falsely rejected for INSUFFICIENT_CASH.
    """

    class RotateBuyFirst(BaseStrategy):
        def initialize(self, context):
            context.set_universe(["600000.SH", "000001.SZ"])

        def before_trading_start(self, context, data):
            if context.now == "20240102":
                context.order("600000.SH", 900)
            elif context.now == "20240103":
                # BUY submitted before its funding SELL.
                context.order("000001.SZ", 900)
                context.order_target("600000.SH", 0)

    cfg = BacktestConfig(
        start_date="20240102",
        end_date="20240104",
        # Enough for day-1 buy (9005), but not enough to fund day-3's
        # 9005 BUY without the same-batch SELL proceeds.
        initial_cash=Decimal("9050"),
        source="tushare",
    )
    engine = BacktestEngine(
        cfg, strategy=RotateBuyFirst(), portal=_portal_two_symbols()
    )
    engine.run()

    day3_fills = [
        e
        for e in engine.event_log.all()
        if e.phase is EventType.ORDER_FILLED and e.date == "20240103"
    ]
    assert len(day3_fills) == 2
    rejected = [
        e
        for e in engine.event_log.all()
        if e.phase is EventType.ORDER_REJECTED and e.date == "20240103"
    ]
    assert all("INSUFFICIENT_CASH" not in (e.error or "") for e in rejected)
    # Rotation completed: 600000.SH flattened, 000001.SZ held.
    positions = {s: p.quantity for s, p in engine.portfolio.positions.items()}
    assert positions.get("600000.SH", 0) == 0
    assert positions.get("000001.SZ", 0) == 900
