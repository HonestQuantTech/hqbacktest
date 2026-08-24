"""Config file loading and validation (task 12).

The TOML schema (one example):

    [start]
    start_date = "20240102"   # YYYYMMDD, required
    end_date   = "20240104"   # YYYYMMDD, required

    [capital]
    initial_cash = "100000"   # Decimal-string, required

    [data]
    source = "tushare"        # name or absolute path, required
    data_root = "~/.hqdata"   # optional, defaults to ~/.hqdata

    [strategy]
    module = "examples.buy_and_hold"  # importable Python module, required
    class_name = "BuyAndHold"           # optional, defaults to first subclass
    kwargs = {}                          # optional, passed to constructor

    [cost_model]
    commission_rate = "0.00025"          # optional
    min_commission  = "5.00"             # optional
    stamp_tax_rate  = "0.001"            # optional
    transfer_fee_rate = "0.0"            # optional

    [output]
    directory = "results/run-1"         # required, will be created

Validation rules are deliberately strict: any unknown key is rejected so
typos surface immediately. The CLI re-emits every user-facing error as
`ConfigError` which becomes a non-zero exit code with a single readable
message on stderr.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import tomli

from ..data.hqdata_portal import DEFAULT_DATA_ROOT
from ..data.validators import validate_yyyymmdd
from ..domain.enums import OrderType
from ..engine.cost_model import DefaultCostModel
from ..engine.rule_set import DEFAULT_V01_RULES, TradingRuleSet
from ..engine.strategy import BaseStrategy


# Hard-coded allowed top-level sections. Anything else is rejected.
_ALLOWED_SECTIONS: tuple[str, ...] = (
    "start",
    "capital",
    "data",
    "strategy",
    "cost_model",
    "output",
)


class ConfigError(ValueError):
    """Raised on any user-facing configuration problem.

    The CLI catches this and prints a single readable line on stderr
    before exiting with a non-zero status.
    """


@dataclass(frozen=True)
class ConfigFile:
    """Validated view of the TOML config the user passed on the CLI.

    All fields are required; defaults live in `BacktestConfig`.
    """

    start_date: str
    end_date: str
    initial_cash: Decimal
    source: str
    strategy_module: str
    output_directory: str
    data_root: str = DEFAULT_DATA_ROOT
    strategy_class: Optional[str] = None
    strategy_kwargs: Dict[str, Any] = field(default_factory=dict)
    cost_overrides: Dict[str, Decimal] = field(default_factory=dict)
    raw_text: str = ""  # exact bytes the user provided (for the audit trail)


# --------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------- #


def load_config_file(path: str) -> ConfigFile:
    """Load and validate a TOML config. Raises `ConfigError` on any issue."""
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigError(f"config file not found: {path}")
    if not file_path.is_file():
        raise ConfigError(f"config path is not a regular file: {path}")
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    try:
        data = tomli.loads(raw.decode("utf-8"))
    except tomli.TOMLDecodeError as exc:
        raise ConfigError(f"config file {path} is not valid TOML: {exc}") from exc
    return _validate(data, raw_text=raw.decode("utf-8"))


def _validate(data: Dict[str, Any], *, raw_text: str) -> ConfigFile:
    """Validate the parsed TOML dict and build a `ConfigFile`.

    All section names must be from `_ALLOWED_SECTIONS`; every required key
    must be present and of the right type.
    """
    unknown_sections = sorted(set(data) - set(_ALLOWED_SECTIONS))
    if unknown_sections:
        raise ConfigError(
            f"unknown config sections: {unknown_sections}; "
            f"allowed: {list(_ALLOWED_SECTIONS)}"
        )

    # ---- [start] ----
    start = data.get("start", {})
    if not isinstance(start, dict):
        raise ConfigError("[start] must be a table")
    start_date = _require_str(start, "start", "start_date")
    end_date = _require_str(start, "end", "end_date")
    try:
        validate_yyyymmdd(start_date, name="start.start_date")
    except Exception as exc:
        raise ConfigError(f"[start].start_date: {exc}") from exc
    try:
        validate_yyyymmdd(end_date, name="start.end_date")
    except Exception as exc:
        raise ConfigError(f"[start].end_date: {exc}") from exc
    if start_date > end_date:
        raise ConfigError(
            f"[start] start_date {start_date} is after end_date {end_date}"
        )

    # ---- [capital] ----
    capital = data.get("capital", {})
    if not isinstance(capital, dict):
        raise ConfigError("[capital] must be a table")
    initial_cash = _require_decimal(
        capital, "capital", "initial_cash", min_value=Decimal("0")
    )

    # ---- [data] ----
    data_sec = data.get("data", {})
    if not isinstance(data_sec, dict):
        raise ConfigError("[data] must be a table")
    source = _require_str(data_sec, "data", "source")
    data_root = data_sec.get("data_root")
    if data_root is None:
        data_root = DEFAULT_DATA_ROOT
    elif not isinstance(data_root, str) or not data_root:
        raise ConfigError("[data].data_root must be a non-empty string")

    # ---- [strategy] ----
    strat = data.get("strategy", {})
    if not isinstance(strat, dict):
        raise ConfigError("[strategy] must be a table")
    strategy_module = _require_str(strat, "strategy", "module")
    strategy_class = strat.get("class_name")
    if strategy_class is not None and not isinstance(strategy_class, str):
        raise ConfigError("[strategy].class_name must be a string")
    strategy_kwargs = strat.get("kwargs", {})
    if strategy_kwargs is None:
        strategy_kwargs = {}
    if not isinstance(strategy_kwargs, dict):
        raise ConfigError("[strategy].kwargs must be a table")

    # ---- [cost_model] (optional) ----
    cost_sec = data.get("cost_model", {})
    if not isinstance(cost_sec, dict):
        raise ConfigError("[cost_model] must be a table")
    cost_overrides: Dict[str, Decimal] = {}
    for key, attr in (
        ("commission_rate", "commission_rate"),
        ("min_commission", "min_commission"),
        ("stamp_tax_rate", "stamp_tax_rate"),
        ("transfer_fee_rate", "transfer_fee_rate"),
    ):
        if key in cost_sec:
            cost_overrides[attr] = _require_decimal(
                cost_sec, "cost_model", key, min_value=Decimal("0")
            )

    # ---- [output] ----
    out = data.get("output", {})
    if not isinstance(out, dict):
        raise ConfigError("[output] must be a table")
    output_directory = _require_str(out, "output", "directory")

    return ConfigFile(
        start_date=start_date,
        end_date=end_date,
        initial_cash=initial_cash,
        source=source,
        strategy_module=strategy_module,
        strategy_class=strategy_class,
        strategy_kwargs=strategy_kwargs,
        cost_overrides=cost_overrides,
        output_directory=output_directory,
        data_root=data_root,
        raw_text=raw_text,
    )


def _require_str(section: Dict[str, Any], section_name: str, key: str) -> str:
    if key not in section:
        raise ConfigError(f"[{section_name}] missing required key {key!r}")
    value = section[key]
    if not isinstance(value, str) or not value:
        raise ConfigError(f"[{section_name}].{key} must be a non-empty string")
    return value


def _require_decimal(
    section: Dict[str, Any],
    section_name: str,
    key: str,
    *,
    min_value: Optional[Decimal] = None,
) -> Decimal:
    if key not in section:
        raise ConfigError(f"[{section_name}] missing required key {key!r}")
    value = section[key]
    if isinstance(value, bool):
        raise ConfigError(f"[{section_name}].{key} must be a number, not bool")
    # Task 16: float is forbidden at the CLI layer too, matching the
    # engine's contract rule 5. Without this check a TOML like
    # `initial_cash = 100000.0` would silently convert to a Decimal via
    # `Decimal(str(float))`, masking the precision concern.
    if isinstance(value, float):
        raise ConfigError(
            f"[{section_name}].{key} must be int/str/Decimal; float is "
            "forbidden (contract rule 5)"
        )
    if isinstance(value, (int, str)):
        try:
            d = Decimal(str(value))
        except Exception as exc:
            raise ConfigError(
                f"[{section_name}].{key}={value!r} is not a valid number: {exc}"
            ) from exc
    elif isinstance(value, Decimal):
        d = value
    else:
        raise ConfigError(
            f"[{section_name}].{key} must be a number, got {type(value).__name__}"
        )
    if min_value is not None and d < min_value:
        raise ConfigError(f"[{section_name}].{key}={d} must be >= {min_value}")
    return d


# --------------------------------------------------------------------- #
# Resolving strategy + BacktestConfig
# --------------------------------------------------------------------- #


def resolve_strategy(config_file: ConfigFile) -> BaseStrategy:
    """Import the user-supplied module and instantiate the strategy class.

    `class_name` is optional; when omitted we use the first `BaseStrategy`
    subclass exported by the module.
    """
    try:
        module = importlib.import_module(config_file.strategy_module)
    except ImportError as exc:
        raise ConfigError(
            f"could not import strategy module {config_file.strategy_module!r}: {exc}"
        ) from exc

    cls: Optional[Type[BaseStrategy]] = None
    if config_file.strategy_class is not None:
        candidate = getattr(module, config_file.strategy_class, None)
        if candidate is None:
            raise ConfigError(
                f"module {config_file.strategy_module!r} has no attribute "
                f"{config_file.strategy_class!r}"
            )
        if not (inspect.isclass(candidate) and issubclass(candidate, BaseStrategy)):
            raise ConfigError(
                f"{config_file.strategy_class!r} is not a BaseStrategy subclass"
            )
        cls = candidate
    else:
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is BaseStrategy:
                continue
            if issubclass(obj, BaseStrategy):
                cls = obj
                break
        if cls is None:
            raise ConfigError(
                f"no BaseStrategy subclass found in {config_file.strategy_module!r}; "
                "either define one or set [strategy].class_name"
            )

    # Constructor kwargs come straight from the user. We do NOT inspect
    # the constructor signature; users are responsible for matching it.
    try:
        return cls(**config_file.strategy_kwargs)
    except TypeError as exc:
        raise ConfigError(
            f"failed to construct {cls.__name__} with kwargs "
            f"{config_file.strategy_kwargs}: {exc}"
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise ConfigError(
            f"unexpected error constructing {cls.__name__}: {exc}"
        ) from exc


def build_backtest_config(
    config_file: ConfigFile,
) -> "BacktestConfig":  # type: ignore[name-defined]
    """Translate the validated config file into a runtime `BacktestConfig`."""
    from ..engine.config import BacktestConfig  # local to break circular import

    cost_model = DefaultCostModel()
    if config_file.cost_overrides:
        cost_model = DefaultCostModel(
            commission_rate=config_file.cost_overrides.get(
                "commission_rate", cost_model.commission_rate
            ),
            min_commission=config_file.cost_overrides.get(
                "min_commission", cost_model.min_commission
            ),
            stamp_tax_rate=config_file.cost_overrides.get(
                "stamp_tax_rate", cost_model.stamp_tax_rate
            ),
            transfer_fee_rate=config_file.cost_overrides.get(
                "transfer_fee_rate", cost_model.transfer_fee_rate
            ),
        )

    return BacktestConfig(
        start_date=config_file.start_date,
        end_date=config_file.end_date,
        initial_cash=config_file.initial_cash,
        source=config_file.source,
        data_root=config_file.data_root,
        rule_set=TradingRuleSet(DEFAULT_V01_RULES),
        cost_model=cost_model,
    )


# Re-export for convenience; `OrderType` import keeps linters happy in
# downstream code that imports from this module.
__all__ = [
    "ConfigError",
    "ConfigFile",
    "build_backtest_config",
    "load_config_file",
    "resolve_strategy",
]
# `OrderType` is intentionally imported above to keep the public-API
# surface in sync with the rest of the engine layer.
_ = OrderType
