"""
Dedup cache for EventCollector fingerprints.

Redis-backed hot set in front of SQLite. The cache is an optimization that
short-circuits SELECT queries for recently-seen fingerprints. SQLite remains
the source of truth: any fingerprint missing from the cache is checked against
SQLite via insert_ignore, which guarantees no duplicate ever lands.

This means: cache loss is a performance event, not a correctness event.
"""

from __future__ import annotations

from typing import Iterable, Protocol


class DedupCache(Protocol):
    """Cache contract. Anything implementing this works with EventCollector."""

    def contains_many(self, fingerprints: Iterable[str]) -> set[str]:
        """Return the subset of fingerprints already in the cache."""

    def add_many(self, fingerprints: Iterable[str]) -> None:
        """Mark fingerprints as seen, with the cache's default TTL."""


class MemoryDedupCache:
    """
    In-process set. Used by tests and as production fallback when Redis is
    down. Bounded only by process memory — fine for collector lifetimes
    measured in days, not weeks.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def contains_many(self, fingerprints: Iterable[str]) -> set[str]:
        return set(fingerprints) & self._seen

    def add_many(self, fingerprints: Iterable[str]) -> None:
        self._seen.update(fingerprints)

    # Helpers for tests / ops
    def __len__(self) -> int:
        return len(self._seen)

    def clear(self) -> None:
        self._seen.clear()


class RedisDedupCache:
    """
    Redis Set with TTL. All fingerprints for a namespace live in one key;
    expiring the key drops the whole set. For announcements at 5m cadence,
    a 48-hour TTL is plenty — the same announcement never reappears beyond
    a same-day re-publish, but the buffer absorbs edge cases.
    """

    def __init__(
        self,
        redis_client,
        namespace: str,
        ttl_seconds: int = 48 * 3600,
    ):
        self._r = redis_client
        self._key = f"dedup:{namespace}"
        self._ttl = ttl_seconds

    def contains_many(self, fingerprints: Iterable[str]) -> set[str]:
        fps = list(fingerprints)
        if not fps:
            return set()
        # SMISMEMBER returns [0/1] in the same order as args. Available since Redis 6.2.
        hits = self._r.smismember(self._key, fps)
        return {fp for fp, hit in zip(fps, hits) if hit}

    def add_many(self, fingerprints: Iterable[str]) -> None:
        fps = list(fingerprints)
        if not fps:
            return
        pipe = self._r.pipeline()
        pipe.sadd(self._key, *fps)
        pipe.expire(self._key, self._ttl)
        pipe.execute()