"""NSE session layer — the only module that talks to NSE.

Public surface: SessionManager. Everything else is internal plumbing
(rate limiter, circuit breaker, header builder) but exposed here too
for the API's /admin/endpoint-health route.
"""
from .manager import SessionManager, FetchError
from .circuit import CircuitBreaker, CircuitOpenError, CircuitState
from .rate_limit import GlobalConcurrencyLimit, PerEndpointRateLimiter

__all__ = [
    "SessionManager",
    "FetchError",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "GlobalConcurrencyLimit",
    "PerEndpointRateLimiter",
]