"""Tests for DataCache keying and isolation across sources."""

from hqbacktest.data.cache import CacheKey, DataCache


def test_put_get_round_trip():
    cache = DataCache()
    key = CacheKey(
        "/data/root", "tushare", "bars", "600000.SH", "", "20240102", "20240110"
    )
    cache.put(key, ["a", "b"])
    assert cache.get(key) == ["a", "b"]
    assert cache.has(key)


def test_cache_distinguishes_data_roots():
    cache = DataCache()
    k1 = CacheKey("/root/A", "tushare", "bars", "600000.SH", "", "20240102", "20240110")
    k2 = CacheKey("/root/B", "tushare", "bars", "600000.SH", "", "20240102", "20240110")
    cache.put(k1, "from_A")
    cache.put(k2, "from_B")
    assert cache.get(k1) == "from_A"
    assert cache.get(k2) == "from_B"


def test_cache_distinguishes_sources():
    cache = DataCache()
    k1 = CacheKey("/root", "tushare", "bars", "600000.SH", "", "20240102", "20240110")
    k2 = CacheKey("/root", "ricequant", "bars", "600000.SH", "", "20240102", "20240110")
    cache.put(k1, "from_tushare")
    cache.put(k2, "from_ricequant")
    assert cache.get(k1) == "from_tushare"
    assert cache.get(k2) == "from_ricequant"


def test_cache_distinguishes_methods():
    cache = DataCache()
    bars_key = CacheKey(
        "/root", "tushare", "bars", "600000.SH", "", "20240102", "20240110"
    )
    factor_key = CacheKey(
        "/root", "tushare", "factor", "600000.SH", "", "20240102", "20240110"
    )
    cache.put(bars_key, [1, 2, 3])
    cache.put(factor_key, __import__("decimal").Decimal("1.05"))
    assert cache.get(bars_key) == [1, 2, 3]


def test_cache_clear():
    cache = DataCache()
    key = CacheKey("/root", "tushare", "bars", "600000.SH", "", "20240102", "20240110")
    cache.put(key, "v")
    cache.clear()
    assert cache.get(key) is None
    assert len(cache) == 0


def test_cache_key_is_hashable_and_distinct():
    a = CacheKey("/root", "tushare", "bars", "600000.SH", "", "20240102", "20240110")
    b = CacheKey("/root", "tushare", "bars", "600000.SH", "", "20240102", "20240110")
    c = CacheKey("/root", "tushare", "bars", "000001.SZ", "", "20240102", "20240110")
    d = CacheKey("/other", "tushare", "bars", "600000.SH", "", "20240102", "20240110")
    assert a == b
    assert hash(a) == hash(b)
    assert a != c
    assert a != d
