"""JSON-friendly serialization for domain models.

Rules (contract §3.2, §6 rule 10):
    - `Decimal` is serialized as a string so the JSON round-trip is lossless.
    - Enums are serialized by name (not value) so renames are caught loudly.
    - Dates stay as `YYYYMMDD` strings.
    - Dataclasses recurse via `dataclasses.asdict`.
"""

import json
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any


def to_jsonable(obj: Any) -> Any:
    """Recursively convert a domain object into JSON-serializable primitives."""
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.name
    if is_dataclass(obj) and not isinstance(obj, type):
        return {key: to_jsonable(value) for key, value in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(key): to_jsonable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(value) for value in obj]
    raise TypeError(f"cannot serialize {type(obj).__name__} to JSON")


def dump_json(obj: Any, *, indent: int = 2, sort_keys: bool = True) -> str:
    """Encode a domain object as a JSON string."""
    return json.dumps(
        to_jsonable(obj),
        ensure_ascii=False,
        indent=indent,
        sort_keys=sort_keys,
        default=str,
    )


def dump_jsonl(objs: Any) -> str:
    """Encode a sequence of objects, one JSON object per line."""
    lines = [
        json.dumps(to_jsonable(obj), ensure_ascii=False, sort_keys=True) for obj in objs
    ]
    return "\n".join(lines)
