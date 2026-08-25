"""In-memory cache for portal queries.

The cache key is the full call identity (source + symbol + field + start + end)
so two different sources never share entries, matching contract §6 rule 2
("one source per run") and the cache rule ("缓存必须包含数据源、标的、
字段和日期范围，不能混用不同数据源").
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterator, Tuple


@dataclass(frozen=True)
class CacheKey:
    """Composite key for one portal call.

    `data_root` is the normalized (resolved, expanded) root directory; including
    it in the key guarantees that two portals pointing at different snapshot
    directories never share cached values ("缓存键必须包含…规范化后的
    data_root").
    """

    data_root: str
    source: str
    method: str
    symbol: str  # "" for non-symbol methods (calendar, universe)
    field: str  # "" for methods that don't take a field
    start: str  # "" if not applicable
    end: str  # "" if not applicable


class DataCache:
    """In-memory key/value cache used by CSV and in-memory portals.

    Values are stored as-is; the portal owns serialization into domain types.
    """

    def __init__(self) -> None:
        self._store: Dict[CacheKey, Any] = {}

    def get(self, key: CacheKey) -> Any:
        return self._store.get(key)

    def has(self, key: CacheKey) -> bool:
        return key in self._store

    def put(self, key: CacheKey, value: Any) -> None:
        self._store[key] = value

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)

    def __iter__(self) -> Iterator[Tuple[CacheKey, Any]]:
        return iter(self._store.items())
