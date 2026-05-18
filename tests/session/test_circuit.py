"""Tests for the circuit breaker.

Pure logic — no network. We patch time.monotonic so cooldown windows
are deterministic. Each test asserts one invariant of the design.
"""
from __future__ import annotations
import pytest

from nse_data.session import circuit as circuit_mod
from nse_data.session.circuit import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
)


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock; tests advance it explicitly."""
    state = {"t": 1000.0}
    monkeypatch.setattr(circuit_mod.time, "monotonic", lambda: state["t"])

    def advance(seconds: float) -> None:
        state["t"] += seconds
    return advance


def test_closed_circuit_allows_calls():
    # Invariant: a fresh breaker never blocks.
    cb = CircuitBreaker(failure_threshold=3)
    cb.before_call("x")  # must not raise


def test_opens_after_threshold_failures(clock):
    # Invariant: N consecutive failures opens the circuit.
    cb = CircuitBreaker(failure_threshold=3, base_cooldown=60)
    for _ in range(3):
        cb.on_failure("x")
    with pytest.raises(CircuitOpenError):
        cb.before_call("x")


def test_success_resets_failure_count(clock):
    # Invariant: success zeroes the counter; threshold is *consecutive*.
    cb = CircuitBreaker(failure_threshold=3)
    cb.on_failure("x")
    cb.on_failure("x")
    cb.on_success("x")
    cb.on_failure("x")
    cb.on_failure("x")
    cb.before_call("x")  # 4 total failures, not 3 in a row → still closed


def test_half_open_after_cooldown(clock):
    # Invariant: once cooldown elapses, the next before_call goes to half-open.
    cb = CircuitBreaker(failure_threshold=2, base_cooldown=60)
    cb.on_failure("x"); cb.on_failure("x")  # opens
    with pytest.raises(CircuitOpenError):
        cb.before_call("x")
    clock(61)
    cb.before_call("x")  # probe allowed
    assert cb.snapshot()["x"]["state"] == CircuitState.HALF_OPEN.value


def test_half_open_failure_doubles_cooldown(clock):
    # Invariant: probe failure re-opens with doubled cooldown (anti-flap).
    cb = CircuitBreaker(failure_threshold=2, base_cooldown=60, max_cooldown=1000)
    cb.on_failure("x"); cb.on_failure("x")
    clock(61)
    cb.before_call("x")     # → half_open
    cb.on_failure("x")      # probe fails → reopen, cooldown 120
    snap = cb.snapshot()["x"]
    assert snap["state"] == CircuitState.OPEN.value
    assert snap["current_cooldown_s"] == 120


def test_cooldown_capped_at_max(clock):
    # Invariant: doubling is bounded.
    cb = CircuitBreaker(failure_threshold=1, base_cooldown=100, max_cooldown=300)
    cb.on_failure("x")                                     # 100
    clock(101); cb.before_call("x"); cb.on_failure("x")    # → 200
    clock(201); cb.before_call("x"); cb.on_failure("x")    # → 400 capped to 300
    clock(301); cb.before_call("x"); cb.on_failure("x")    # stays 300
    assert cb.snapshot()["x"]["current_cooldown_s"] == 300


def test_half_open_success_closes_and_resets(clock):
    # Invariant: recovery resets cooldown — next outage starts fresh at base.
    cb = CircuitBreaker(failure_threshold=2, base_cooldown=60)
    cb.on_failure("x"); cb.on_failure("x")
    clock(61)
    cb.before_call("x")
    cb.on_success("x")
    snap = cb.snapshot()["x"]
    assert snap["state"] == CircuitState.CLOSED.value
    assert snap["consecutive_failures"] == 0
    assert snap["current_cooldown_s"] == 60


def test_per_endpoint_isolation():
    # Invariant: one bad endpoint doesn't affect another's circuit.
    cb = CircuitBreaker(failure_threshold=2)
    cb.on_failure("a"); cb.on_failure("a")
    with pytest.raises(CircuitOpenError):
        cb.before_call("a")
    cb.before_call("b")  # untouched