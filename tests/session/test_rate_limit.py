"""Tests for the rate limiter.

Token-bucket logic is timing-sensitive, so we patch time.monotonic AND
time.sleep in the rate_limit module to make tests fully deterministic.
"""
from __future__ import annotations
import threading
import pytest

from nse_data.session import rate_limit as rl_mod
from nse_data.session.rate_limit import (
    GlobalConcurrencyLimit,
    PerEndpointRateLimiter,
)


@pytest.fixture
def fake_clock(monkeypatch):
    """time.monotonic + time.sleep replaced with a controllable clock.

    fake_sleep advances the virtual clock; real time never passes. That
    lets us test refill behavior in microseconds instead of seconds.
    """
    state = {"t": 1000.0}
    monkeypatch.setattr(rl_mod.time, "monotonic", lambda: state["t"])
    monkeypatch.setattr(rl_mod.time, "sleep", lambda s: state.update(t=state["t"] + s))

    def advance(seconds: float) -> None:
        state["t"] += seconds
    return advance


def test_unconfigured_endpoint_passes_through(fake_clock):
    # Invariant: no configure() → no limit (fail-open).
    limiter = PerEndpointRateLimiter()
    for _ in range(1000):
        with limiter.slot("never_configured"):
            pass


def test_burst_capacity(fake_clock):
    # Invariant: fresh bucket allows `capacity` calls back-to-back.
    limiter = PerEndpointRateLimiter()
    limiter.configure("x", per_minute=60, burst=5)
    for _ in range(5):
        with limiter.slot("x"):
            pass
    # 6th call has no tokens; timeout=0 must fail fast.
    with pytest.raises(TimeoutError):
        with limiter.slot("x", timeout=0.0):
            pass


def test_refill_after_wait(fake_clock):
    # Invariant: tokens refill at the configured rate.
    limiter = PerEndpointRateLimiter()
    limiter.configure("x", per_minute=60, burst=1)  # 1 token / sec
    with limiter.slot("x"):
        pass
    fake_clock(1.5)  # advance virtual time; bucket regenerates
    with limiter.slot("x", timeout=0.0):
        pass


def test_global_concurrency_caps_in_flight():
    # Invariant: never more than N holders of the global slot simultaneously.
    import time as real_time
    g = GlobalConcurrencyLimit(max_concurrent=2)
    in_flight = 0
    max_seen = 0
    lock = threading.Lock()
    release = threading.Event()

    def worker():
        nonlocal in_flight, max_seen
        with g.slot(timeout=2):
            with lock:
                in_flight += 1
                max_seen = max(max_seen, in_flight)
            release.wait(timeout=2)
            with lock:
                in_flight -= 1

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()

    for _ in range(50):
        with lock:
            if in_flight == 2:
                break
        real_time.sleep(0.01)

    assert max_seen == 2
    release.set()
    for t in threads:
        t.join(timeout=2)


def test_global_slot_timeout():
    # Invariant: callers don't wait forever.
    g = GlobalConcurrencyLimit(max_concurrent=1)
    with g.slot():
        with pytest.raises(TimeoutError):
            with g.slot(timeout=0.05):
                pass


def test_endpoint_isolation(fake_clock):
    # Invariant: one endpoint's exhaustion doesn't affect another's.
    limiter = PerEndpointRateLimiter()
    limiter.configure("a", per_minute=60, burst=1)
    limiter.configure("b", per_minute=60, burst=1)
    with limiter.slot("a"):
        pass
    with limiter.slot("b", timeout=0.0):
        pass