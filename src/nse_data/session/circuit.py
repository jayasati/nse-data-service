
"""Per-endpoint circuit breaker.

States:
- closed: normal operation, requests pass through.
- open: requests fail fast (CircuitOpenError) without hitting NSE.
- half_open: after cooldown expires, exactly ONE probe is allowed.
    success → close, reset cooldown.
    failure → re-open with cooldown doubled (capped at max_cooldown).

Why per-endpoint: NSE often has one bad path while others are fine. A global
breaker would punish healthy endpoints for one sick one.

Why fail-fast: a request to a dead endpoint typically hangs for the full
20s timeout WHILE holding a global concurrency slot. Five such requests
stalled in parallel = no other collector can run. The breaker turns those
hangs into instant rejections.
"""
from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when the circuit is open and the call was short-circuited."""


@dataclass
class _CircuitData:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    current_cooldown: float = 60.0
    lock: threading.Lock = field(default_factory=threading.Lock)


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        base_cooldown: float = 60.0,
        max_cooldown: float = 1800.0,
    ):
        self.failure_threshold = failure_threshold
        self.base_cooldown = base_cooldown
        self.max_cooldown = max_cooldown
        self._circuits: dict[str, _CircuitData] = {}
        self._registry_lock = threading.Lock()

    def _get(self, name: str) -> _CircuitData:
        c = self._circuits.get(name)
        if c is None:
            with self._registry_lock:
                c = self._circuits.setdefault(
                    name, _CircuitData(current_cooldown=self.base_cooldown)
                )
        return c

    def before_call(self, name: str) -> None:
        """Raise CircuitOpenError if the call should be short-circuited."""
        c = self._get(name)
        with c.lock:
            if c.state == CircuitState.OPEN:
                if time.monotonic() - c.opened_at >= c.current_cooldown:
                    c.state = CircuitState.HALF_OPEN  # let one probe through
                else:
                    raise CircuitOpenError(f"{name} circuit open")

    def on_success(self, name: str) -> None:
        c = self._get(name)
        with c.lock:
            c.state = CircuitState.CLOSED
            c.consecutive_failures = 0
            c.current_cooldown = self.base_cooldown

    def on_failure(self, name: str) -> None:
        c = self._get(name)
        with c.lock:
            c.consecutive_failures += 1
            if c.state == CircuitState.HALF_OPEN:
                # probe failed → re-open with longer cooldown
                c.current_cooldown = min(c.current_cooldown * 2, self.max_cooldown)
                c.state = CircuitState.OPEN
                c.opened_at = time.monotonic()
            elif c.consecutive_failures >= self.failure_threshold:
                c.state = CircuitState.OPEN
                c.opened_at = time.monotonic()

    def snapshot(self) -> dict[str, dict]:
        """For /admin/endpoint-health and the endpoint_health table."""
        out = {}
        for name, c in self._circuits.items():
            with c.lock:
                out[name] = {
                    "state": c.state.value,
                    "consecutive_failures": c.consecutive_failures,
                    "current_cooldown_s": c.current_cooldown,
                    "opened_at": c.opened_at,
                }
        return out