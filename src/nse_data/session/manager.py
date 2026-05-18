# src/nse_data/session/manager.py
"""NSE session manager — the only place in the codebase that talks to NSE.

Lifecycle of one call:
    circuit.before_call()        # fail fast if endpoint is sick
    global_concurrency.slot()    # at most N in-flight to NSE total
    rate_limit.slot(endpoint)    # respect per-endpoint quota
    ensure_warm()                # cookies, refreshed every 15m
    httpx.request()
        ├── 401/403 → re-warm once, retry once
        ├── 429    → backoff, retry up to 3x
        ├── 5xx    → propagate (circuit counts it)
        └── 2xx    → return
    circuit.on_success/failure

Collectors never touch httpx, cookies, or headers directly. They call
get_json / get_text / get_bytes with an endpoint name and Referer.
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Any

import httpx

from .circuit import CircuitBreaker, CircuitOpenError
from .headers import NSE_ORIGIN, build_headers
from .rate_limit import GlobalConcurrencyLimit, PerEndpointRateLimiter

log = logging.getLogger(__name__)

COOKIE_TTL_SEC = 15 * 60
WARMUP_TIMEOUT = 10.0
REQUEST_TIMEOUT = 20.0
MAX_RETRIES_ON_AUTH = 1
MAX_RETRIES_ON_429 = 3


class FetchError(RuntimeError):
    pass


class SessionManager:
    def __init__(
        self,
        global_concurrency: int = 4,
        circuit_failure_threshold: int = 5,
        client: httpx.Client | None = None,
    ):
        # `client` injectable for tests; production builds its own.
        self._client = client or httpx.Client(
            base_url=NSE_ORIGIN,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            http2=False,
        )
        self._global = GlobalConcurrencyLimit(max_concurrent=global_concurrency)
        self._rate = PerEndpointRateLimiter()
        self._circuit = CircuitBreaker(failure_threshold=circuit_failure_threshold)

        self._cookies_valid_until: float = 0.0
        self._warmup_lock = threading.Lock()
        self._warmup_count = 0  # exposed for tests
    # ---------- exposed for wiring & ops ----------

    @property
    def rate_limiter(self) -> PerEndpointRateLimiter:
        return self._rate
    
    @property
    def warmup_count(self) -> int:
        return self._warmup_count

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._circuit

    def close(self) -> None:
        self._client.close()

    # ---------- the API collectors use ----------

    def get_json(
        self,
        endpoint_name: str,
        path: str,
        referer: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._request("GET", endpoint_name, path, referer, params, absolute=False).json()

    def get_text(
        self,
        endpoint_name: str,
        path: str,
        referer: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        return self._request("GET", endpoint_name, path, referer, params, absolute=False).text

    def get_bytes(
        self,
        endpoint_name: str,
        url: str,
        referer: str | None = None,
    ) -> bytes:
        # For absolute URLs: bhavcopy on nsearchives.nseindia.com, PDF attachments, etc.
        return self._request("GET", endpoint_name, url, referer, None, absolute=True).content

    # ---------- internals ----------

    def _ensure_warm(self, force: bool = False) -> None:
        if not force and time.monotonic() < self._cookies_valid_until:
            return
        with self._warmup_lock:
            # Double-checked: another thread may have just warmed up while we waited.
            if not force and time.monotonic() < self._cookies_valid_until:
                return
            log.info("warming nse session")
            try:
                self._client.cookies.clear()
                r1 = self._client.get(
                    "/", headers=build_headers(), timeout=WARMUP_TIMEOUT
                )
                r1.raise_for_status()
                # Second hop: some endpoints (notably option-chain) only work after
                # the bm_* cookies set by a real market-data page have been seen.
                self._client.get(
                    "/market-data/live-equity-market",
                    headers=build_headers(referer=NSE_ORIGIN + "/"),
                    timeout=WARMUP_TIMEOUT,
                )
                self._cookies_valid_until = time.monotonic() + COOKIE_TTL_SEC
                self._warmup_count += 1 
            except Exception as e:
                self._cookies_valid_until = 0.0
                raise FetchError(f"warm-up failed: {e}") from e

    def _request(
        self,
        method: str,
        endpoint_name: str,
        path_or_url: str,
        referer: str | None,
        params: dict[str, Any] | None,
        absolute: bool,
    ) -> httpx.Response:
        # Order matters: cheap checks first.
        self._circuit.before_call(endpoint_name)  # raises CircuitOpenError

        with self._global.slot():
            with self._rate.slot(endpoint_name):
                try:
                    resp = self._do(method, endpoint_name, path_or_url, referer, params, absolute)
                    self._circuit.on_success(endpoint_name)
                    return resp
                except CircuitOpenError:
                    raise  # circuit already-open, don't count as another failure
                except Exception as e:
                    self._circuit.on_failure(endpoint_name)
                    raise FetchError(f"{endpoint_name}: {e}") from e

    def _do(
        self,
        method: str,
        endpoint_name: str,
        path_or_url: str,
        referer: str | None,
        params: dict[str, Any] | None,
        absolute: bool,
    ) -> httpx.Response:
        self._ensure_warm()

        attempts_auth = 0
        attempts_429 = 0
        backoff = 1.0

        while True:
            headers = build_headers(referer=referer)
            resp = self._client.request(method, path_or_url, headers=headers, params=params)

            if resp.status_code in (401, 403):
                if attempts_auth >= MAX_RETRIES_ON_AUTH:
                    resp.raise_for_status()
                attempts_auth += 1
                log.warning(
                    "auth %d on %s — re-warming and retrying", resp.status_code, endpoint_name
                )
                self._ensure_warm(force=True)
                continue

            if resp.status_code == 429:
                if attempts_429 >= MAX_RETRIES_ON_429:
                    resp.raise_for_status()
                attempts_429 += 1
                retry_after = float(resp.headers.get("Retry-After", backoff))
                log.warning("429 on %s — sleeping %.1fs", endpoint_name, retry_after)
                time.sleep(retry_after)
                backoff = min(backoff * 2, 30.0)
                continue

            resp.raise_for_status()  # 5xx/4xx → exception → circuit counts it
            return resp