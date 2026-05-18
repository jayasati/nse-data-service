"""Integration tests for SessionManager.

httpx.MockTransport simulates NSE end-to-end. The handler we pass to it
is called for *every* request — warm-up hops and real API calls alike.
That gives us total control over what NSE "returns."
"""
from __future__ import annotations
import httpx
import pytest

from nse_data.session import manager as manager_mod
from nse_data.session.circuit import CircuitOpenError
from nse_data.session.manager import FetchError, SessionManager


def make_manager(handler, **kwargs) -> SessionManager:
    client = httpx.Client(
        base_url="https://www.nseindia.com",
        transport=httpx.MockTransport(handler),
        timeout=2,
    )
    return SessionManager(client=client, **kwargs)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Make time.sleep a no-op so 429 backoff tests run in milliseconds."""
    monkeypatch.setattr(manager_mod.time, "sleep", lambda s: None)


def test_warmup_then_fetch_success():
    # Invariant: cold manager does warm-up (2 hops) before the first call.
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/announcements":
            return httpx.Response(200, json={"data": [1, 2, 3]})
        return httpx.Response(200, text="<html/>")  # warm-up pages

    sm = make_manager(handler)
    result = sm.get_json("announcements", "/api/announcements")

    assert result == {"data": [1, 2, 3]}
    assert calls == ["/", "/market-data/live-equity-market", "/api/announcements"]
    assert sm.warmup_count == 1
    sm.close()


def test_warm_session_reused_within_ttl():
    # Invariant: warm-up happens once; later calls in the TTL window skip it.
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.startswith("/api/"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, text="<html/>")

    sm = make_manager(handler)
    sm.get_json("a", "/api/one")
    sm.get_json("b", "/api/two")
    sm.get_json("c", "/api/three")

    assert sm.warmup_count == 1
    api_calls = [p for p in calls if p.startswith("/api/")]
    assert api_calls == ["/api/one", "/api/two", "/api/three"]
    sm.close()


def test_401_triggers_rewarm_and_retry():
    # Invariant: stale cookies (401) → forced re-warm + retry once.
    # This is THE codepath that breaks silently.
    state = {"served_401": False}
    api_calls = 0
    warmup_calls = 0

    def handler(request):
        nonlocal api_calls, warmup_calls
        if request.url.path in ("/", "/market-data/live-equity-market"):
            warmup_calls += 1
            return httpx.Response(200, text="<html/>")
        if request.url.path == "/api/x":
            api_calls += 1
            if not state["served_401"]:
                state["served_401"] = True
                return httpx.Response(401)
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    sm = make_manager(handler)
    result = sm.get_json("x", "/api/x")

    assert result == {"ok": True}
    assert sm.warmup_count == 2     # initial + forced re-warm
    assert api_calls == 2           # 401 then success
    assert warmup_calls == 4        # 2 hops × 2 warm-ups
    sm.close()


def test_persistent_401_propagates():
    # Invariant: one re-warm retry, then bubble up. No infinite loop.
    def handler(request):
        if request.url.path.startswith("/api/"):
            return httpx.Response(403)
        return httpx.Response(200, text="<html/>")

    sm = make_manager(handler)
    with pytest.raises(FetchError):
        sm.get_json("x", "/api/blocked")
    sm.close()


def test_429_backoff_and_retry():
    # Invariant: 429 backs off (respecting Retry-After) and retries.
    call_count = {"n": 0}
    def handler(request):
        if request.url.path.startswith("/api/"):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return httpx.Response(429, headers={"Retry-After": "0.1"})
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, text="<html/>")

    sm = make_manager(handler)
    result = sm.get_json("x", "/api/throttled")
    assert result == {"ok": True}
    assert call_count["n"] == 3
    sm.close()


def test_open_circuit_short_circuits_without_wrapping():
    # Invariant: CircuitOpenError is raised cleanly, NOT wrapped in FetchError,
    # so callers can tell "endpoint sick" from "this call failed."
    def handler(request):
        if request.url.path.startswith("/api/"):
            return httpx.Response(500)
        return httpx.Response(200, text="<html/>")

    sm = make_manager(handler, circuit_failure_threshold=2)
    for _ in range(2):
        with pytest.raises(FetchError):
            sm.get_json("bad", "/api/bad")

    with pytest.raises(CircuitOpenError):
        sm.get_json("bad", "/api/bad")
    sm.close()


def test_referer_propagates_to_request():
    # Invariant: caller's Referer reaches NSE — #1 cause of real 401s.
    seen = {}
    def handler(request):
        if request.url.path == "/api/needs-referer":
            seen["referer"] = request.headers.get("Referer", "")
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, text="<html/>")

    sm = make_manager(handler)
    sm.get_json("x", "/api/needs-referer",
                referer="https://www.nseindia.com/option-chain")
    assert seen["referer"] == "https://www.nseindia.com/option-chain"
    sm.close()


def test_warmup_failure_propagates():
    # Invariant: if warm-up itself fails, the caller learns immediately.
    def handler(request):
        return httpx.Response(503)
    sm = make_manager(handler)
    with pytest.raises(FetchError):
        sm.get_json("anything", "/api/anything")
    sm.close()