"""
Signal dedup — stop the same setup re-firing every minute (task 4.7).

The detector runs once a minute. A `long_buildup` that's true at 10:00 is
almost certainly still true at 10:01, 10:02, ... — without a guard the same
alert would land in `signals` (and on Telegram) 30 times in half an hour.

The guard is a per-(symbol, signal_type) fingerprint with a 30-minute TTL:

    key  = sigdedup:{symbol}:{signal_type}
    TTL  = 1800s

`claim()` is an atomic check-and-set: it returns True the *first* time it sees
a fingerprint (the caller should write the signal) and False while the key is
still live (the caller drops it). After 30 minutes the key expires and the same
setup may fire again — a deliberate "still going" re-alert, not a duplicate.

Redis is the normal backend (SET NX EX is atomic, so two detector ticks racing
can't both win). When Redis is down we fall back to an in-process dict so the
detector still de-dupes within a single run — consistent with the rest of the
codebase treating Redis as an optimization, not a correctness dependency
(see storage/cache.py).
"""

from __future__ import annotations

import time

# 30-minute fingerprint lifetime (task 4.7).
DEDUP_TTL_SECS = 30 * 60


def fingerprint_key(symbol: str, signal_type: str) -> str:
    return f"sigdedup:{symbol}:{signal_type}"


class SignalDedup:
    """Check-and-set dedup over Redis, with an in-process fallback.

    `redis_client` may be None (or any object exposing redis-py's
    `set(key, value, nx=, ex=)`); when absent the memory dict is used.
    """

    def __init__(self, redis_client=None, ttl_seconds: int = DEDUP_TTL_SECS):
        self._r = redis_client
        self._ttl = ttl_seconds
        self._mem: dict[str, float] = {}

    def claim(self, symbol: str, signal_type: str, *, now: float | None = None) -> bool:
        """Reserve a fingerprint. True if fresh (fire it), False if duplicate.

        `now` (epoch seconds) is only consulted by the memory fallback and lets
        tests drive expiry deterministically; Redis uses its own clock via TTL.
        """
        key = fingerprint_key(symbol, signal_type)

        if self._r is not None:
            try:
                # NX => only set if absent; returns truthy on a fresh claim,
                # None when the key already exists. EX applies the TTL atomically.
                return bool(self._r.set(key, "1", nx=True, ex=self._ttl))
            except Exception:
                # Redis hiccup mid-run — degrade to the memory guard rather than
                # letting a transient failure unleash duplicate alerts.
                pass

        return self._claim_memory(key, now)

    def _claim_memory(self, key: str, now: float | None) -> bool:
        t = time.time() if now is None else now
        expiry = self._mem.get(key)
        if expiry is not None and expiry > t:
            return False
        self._mem[key] = t + self._ttl
        return True
