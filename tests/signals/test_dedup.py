"""Signal dedup guard (signals/dedup.py)."""

from __future__ import annotations

from nse_data.signals.dedup import DEDUP_TTL_SECS, SignalDedup, fingerprint_key

from .conftest import FakeRedis


def test_fingerprint_key_format():
    assert fingerprint_key("ACME", "long_buildup") == "sigdedup:ACME:long_buildup"


def test_redis_claim_first_wins_then_blocks():
    r = FakeRedis()
    d = SignalDedup(r)
    assert d.claim("ACME", "long_buildup") is True     # fresh
    assert d.claim("ACME", "long_buildup") is False    # duplicate within TTL
    # Distinct type / symbol are independent fingerprints.
    assert d.claim("ACME", "breakout_52wh") is True
    assert d.claim("OTHER", "long_buildup") is True


def test_redis_claim_sets_ttl():
    r = FakeRedis()
    SignalDedup(r).claim("ACME", "long_buildup")
    assert r.ttl("sigdedup:ACME:long_buildup") == DEDUP_TTL_SECS


def test_memory_fallback_when_no_redis():
    d = SignalDedup(None, ttl_seconds=1800)
    assert d.claim("ACME", "long_buildup", now=1000.0) is True
    assert d.claim("ACME", "long_buildup", now=1500.0) is False   # still live
    assert d.claim("ACME", "long_buildup", now=3000.0) is True    # past TTL


def test_redis_error_degrades_to_memory():
    class Boom:
        def set(self, *a, **k):
            raise RuntimeError("redis down")

    d = SignalDedup(Boom())
    assert d.claim("ACME", "long_buildup", now=1000.0) is True
    assert d.claim("ACME", "long_buildup", now=1000.0) is False
