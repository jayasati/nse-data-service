
"""Rate limiting for NSE traffic.

Two layers:
1. GlobalConcurrencyLimit — bounded semaphore, never more than N in-flight
   requests to NSE at once. Protects against many scheduled jobs firing in
   the same second.
2. PerEndpointRateLimiter — token bucket per endpoint name. Stops one chatty
   endpoint (e.g. option_chain at 12/min × 4 symbols) from monopolizing the
   global semaphore.

Both are thread-safe; APScheduler runs jobs on threads, not asyncio.
"""
from __future__ import annotations
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass


class GlobalConcurrencyLimit:
    """Caps total in-flight NSE requests across all collectors."""

    def __init__(self, max_concurrent: int = 4):
        self._sem = threading.BoundedSemaphore(max_concurrent)

    @contextmanager
    def slot(self, timeout: float = 30.0):
        if not self._sem.acquire(timeout=timeout):
            raise TimeoutError("Could not acquire global NSE slot in time")
        try:
            yield
        finally:
            self._sem.release()


@dataclass
class _Bucket:
    capacity: float
    refill_per_sec: float
    tokens: float
    last_refill: float
    lock: threading.Lock


class PerEndpointRateLimiter:
    """Token bucket per endpoint_name.

    Usage:
        limiter.configure('option_chain', per_minute=12)
        with limiter.slot('option_chain'):
            ...

    Unconfigured endpoints are unlimited (fail-open). This is deliberate:
    adding a new endpoint shouldn't require a rate-limiter entry to work.
    """

    def __init__(self):
        self._buckets: dict[str, _Bucket] = {}
        self._configure_lock = threading.Lock()

    def configure(self, name: str, per_minute: float, burst: int | None = None) -> None:
        capacity = float(burst if burst is not None else max(1, int(per_minute)))
        refill = per_minute / 60.0
        with self._configure_lock:
            self._buckets[name] = _Bucket(
                capacity=capacity,
                refill_per_sec=refill,
                tokens=capacity,
                last_refill=time.monotonic(),
                lock=threading.Lock(),
            )

    @contextmanager
    def slot(self, name: str, timeout: float = 30.0):
        bucket = self._buckets.get(name)
        if bucket is None:
            yield
            return
        deadline = time.monotonic() + timeout
        while True:
            with bucket.lock:
                now = time.monotonic()
                elapsed = now - bucket.last_refill
                bucket.tokens = min(
                    bucket.capacity,
                    bucket.tokens + elapsed * bucket.refill_per_sec,
                )
                bucket.last_refill = now
                if bucket.tokens >= 1.0:
                    bucket.tokens -= 1.0
                    break
                wait = (1.0 - bucket.tokens) / bucket.refill_per_sec
            if time.monotonic() + wait > deadline:
                raise TimeoutError(f"Rate limit timeout for endpoint {name}")
            time.sleep(min(wait, 0.5))
        yield  # token already consumed; nothing to release on exit