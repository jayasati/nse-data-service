"""Unit tests for the dedup cache abstraction."""

from __future__ import annotations

from nse_data.storage.cache import MemoryDedupCache


def test_empty_cache_returns_no_hits():
    cache = MemoryDedupCache()
    assert cache.contains_many(["a", "b", "c"]) == set()


def test_add_then_contains():
    cache = MemoryDedupCache()
    cache.add_many(["a", "b"])
    assert cache.contains_many(["a", "b", "c"]) == {"a", "b"}


def test_add_is_idempotent():
    cache = MemoryDedupCache()
    cache.add_many(["a", "b"])
    cache.add_many(["a", "b", "c"])
    assert len(cache) == 3


def test_empty_inputs_are_safe():
    cache = MemoryDedupCache()
    cache.add_many([])
    assert cache.contains_many([]) == set()