"""BacktestResult: full audit-trail summary returned by `BacktestEngine.run()`.

Tasks 5 / 7 / 9 / 10 progressively add fields. v0.1 includes:
    * `config_snapshot`        - serialised `BacktestConfig` (asdict).
    * `event_log`              - the run's `EventLog`.
    * `trading_days`           - dates actually iterated.
    * `adjustment_policy`      - the policy that was applied (always "none"
                                 in v0.1; recorded for the audit trail).
    * `factor_diagnostics`     - factor-quality observations; empty in v0.1
                                 because factor_total_return is disabled.
    * `equity_curve`           - per-day `EquityPoint` snapshot.
    * `orders_table`           - one row per order.
    * `fills_table`            - one row per fill.
    * `positions_table`        - per-day per-symbol snapshot.
    * `costs_table`            - per-fill cost breakdown.
    * `metrics`                - `PerformanceMetrics` computed from the run.

`save(dir)` writes one CSV per table plus a `summary.json` containing
the metrics and config snapshot. `load(dir)` rehydrates the tables so
auditors can rebuild the equity curve from disk.

Failed runs never produce a `BacktestResult`: `BacktestEngine.run()`
aborts with `RunFailed` instead.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..domain.money import quantize_cash
from .corporate_actions import V01_ADJUSTMENT_POLICY, FactorDiagnostic
from .events import EventLog
from .metrics import EquityPoint, PerformanceMetrics


_EQUITY_COLUMNS: tuple[str, ...] = (
    "date",
    "cash",
    "market_value",
    "total_equity",
    "daily_return",
    "drawdown",
)
_ORDERS_COLUMNS: tuple[str, ...] = (
    "order_id",
    "symbol",
    "side",
    "quantity",
    "order_type",
    "status",
    "created_at",
    "created_session",
    "filled_at",
    "avg_fill_price",
    "commission_total",
    "reject_reason",
    "reject_detail",
)
_FILLS_COLUMNS: tuple[str, ...] = (
    "fill_id",
    "order_id",
    "symbol",
    "side",
    "quantity",
    "price",
    "amount",
    "commission",
    "stamp_tax",
    "other_fee",
    "filled_at",
    "session",
)
_POSITIONS_COLUMNS: tuple[str, ...] = (
    "date",
    "symbol",
    "quantity",
    "sellable_quantity",
    "avg_cost",
    "market_price",
    "market_value",
)
_COSTS_COLUMNS: tuple[str, ...] = (
    "date",
    "fill_id",
    "order_id",
    "symbol",
    "side",
    "quantity",
    "gross",
    "commission",
    "stamp_tax",
    "other_fee",
    "net",
)


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert a domain object into JSON primitives."""
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict

        return _to_jsonable(asdict(obj))
    return str(obj)


@dataclass
class BacktestResult:
    """What `BacktestEngine.run()` returns after a successful run."""

    config_snapshot: dict
    event_log: EventLog
    trading_days: List[str] = field(default_factory=list)
    adjustment_policy: str = V01_ADJUSTMENT_POLICY
    data_version: Dict[str, str] = field(default_factory=dict)
    factor_diagnostics: List[FactorDiagnostic] = field(default_factory=list)
    equity_curve: List[EquityPoint] = field(default_factory=list)
    orders_table: List[Dict[str, Any]] = field(default_factory=list)
    fills_table: List[Dict[str, Any]] = field(default_factory=list)
    positions_table: List[Dict[str, Any]] = field(default_factory=list)
    costs_table: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Optional[PerformanceMetrics] = None

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #

    def save(self, output_dir: str) -> None:
        """Persist the result to `output_dir` as CSV + JSON."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        _write_csv(
            out / "equity_curve.csv",
            _EQUITY_COLUMNS,
            (self._equity_row(p) for p in self.equity_curve),
        )
        _write_csv(out / "orders.csv", _ORDERS_COLUMNS, iter(self.orders_table))
        _write_csv(out / "fills.csv", _FILLS_COLUMNS, iter(self.fills_table))
        _write_csv(
            out / "positions.csv", _POSITIONS_COLUMNS, iter(self.positions_table)
        )
        _write_csv(out / "costs.csv", _COSTS_COLUMNS, iter(self.costs_table))
        summary = {
            "config_snapshot": _to_jsonable(self.config_snapshot),
            "trading_days": list(self.trading_days),
            "adjustment_policy": self.adjustment_policy,
            "data_version": dict(self.data_version),
            "factor_diagnostics": _to_jsonable(self.factor_diagnostics),
            "metrics": _to_jsonable(self.metrics),
        }
        (out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        # The full audit trail is part of the result.
        with (out / "events.jsonl").open("w", encoding="utf-8") as fh:
            for event in self.event_log.all():
                fh.write(
                    json.dumps(_to_jsonable(event.to_dict()), ensure_ascii=False) + "\n"
                )

    @classmethod
    def load(cls, output_dir: str) -> "BacktestResult":
        """Rehydrate a `BacktestResult` from disk. Order/fill IDs and dates
        are preserved as strings so the round-trip preserves semantics."""
        out = Path(output_dir)
        if not out.exists():
            raise FileNotFoundError(f"output directory not found: {output_dir}")
        summary_path = out / "summary.json"
        if not summary_path.exists():
            raise FileNotFoundError(f"summary.json not found in {output_dir}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        return cls(
            config_snapshot=summary.get("config_snapshot", {}),
            event_log=_read_events(out / "events.jsonl"),
            trading_days=list(summary.get("trading_days", [])),
            adjustment_policy=summary.get("adjustment_policy", V01_ADJUSTMENT_POLICY),
            data_version=dict(summary.get("data_version", {})),
            factor_diagnostics=[
                FactorDiagnostic(
                    symbol=d["symbol"],
                    date=d["date"],
                    kind=d["kind"],
                    detail=d["detail"],
                )
                for d in summary.get("factor_diagnostics", [])
            ],
            equity_curve=[
                EquityPoint(
                    date=row["date"],
                    cash=Decimal(row["cash"]),
                    market_value=Decimal(row["market_value"]),
                    total_equity=Decimal(row["total_equity"]),
                    daily_return=Decimal(row["daily_return"]),
                    drawdown=Decimal(row["drawdown"]),
                )
                for row in _read_csv(out / "equity_curve.csv")
            ],
            orders_table=list(_read_csv(out / "orders.csv")),
            fills_table=list(_read_csv(out / "fills.csv")),
            positions_table=list(_read_csv(out / "positions.csv")),
            costs_table=list(_read_csv(out / "costs.csv")),
            metrics=_metrics_from_json(summary.get("metrics")),
        )

    # ------------------------------------------------------------------ #
    # Row builders
    # ------------------------------------------------------------------ #

    def _equity_row(self, point: EquityPoint) -> Dict[str, Any]:
        return {
            "date": point.date,
            "cash": str(point.cash),
            "market_value": str(point.market_value),
            "total_equity": str(point.total_equity),
            "daily_return": str(point.daily_return),
            "drawdown": str(point.drawdown),
        }


# --------------------------------------------------------------------- #
# CSV helpers (stdlib only, no pandas)
# --------------------------------------------------------------------- #


def _optional_decimal(value: Any) -> Optional[Decimal]:
    return Decimal(value) if value is not None else None


def _metrics_from_json(data: Any) -> Optional[PerformanceMetrics]:
    """Rebuild typed `PerformanceMetrics` from the summary JSON payload."""
    if data is None:
        return None
    return PerformanceMetrics(
        total_return=Decimal(data["total_return"]),
        annualized_return=_optional_decimal(data.get("annualized_return")),
        daily_volatility=_optional_decimal(data.get("daily_volatility")),
        annualized_volatility=_optional_decimal(data.get("annualized_volatility")),
        sharpe_ratio=_optional_decimal(data.get("sharpe_ratio")),
        max_drawdown=Decimal(data["max_drawdown"]),
        turnover=Decimal(data["turnover"]),
        trade_count=int(data["trade_count"]),
        win_rate=_optional_decimal(data.get("win_rate")),
        notes=tuple(data.get("notes", ())),
    )


def _read_events(path: Path) -> EventLog:
    """Rebuild the run's event log from `events.jsonl` (missing => empty)."""
    from ..domain.enums import EventType

    from .events import EngineEvent

    log = EventLog()
    if not path.exists():
        return log
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            log.record(
                EngineEvent(
                    date=payload["date"],
                    phase=EventType[payload["phase"]],
                    order_id=payload.get("order_id"),
                    fill_id=payload.get("fill_id"),
                    error=payload.get("error"),
                    detail=payload.get("detail", ""),
                )
            )
    return log


def _write_csv(path: Path, columns: tuple, rows) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


# Re-export quantize_cash so callers can use it via the result module.
__all__ = [
    "BacktestResult",
    "EquityPoint",
    "PerformanceMetrics",
    "V01_ADJUSTMENT_POLICY",
    "quantize_cash",
]
