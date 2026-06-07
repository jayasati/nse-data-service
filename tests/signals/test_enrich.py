"""Live-context enrichment (signals/enrich.py)."""

from __future__ import annotations

from nse_data.signals import enrich

from .conftest import FakeRedis


def test_reads_redis_hash_and_parses_types():
    r = FakeRedis()
    r.hset("ind:ACME", mapping={
        "updated_at": "2025-06-02T10:00:00+05:30",
        "vwap": "110.5", "vwap_slope": "1.0", "atr_14_daily": "2.0",
        "atr_14_5m": "0.3", "rsi_5m": "72.0",
        "price_vs_vwap": "above", "trend_regime": "strong_uptrend",
        "momentum_state": "overbought",
    })
    ctx = enrich.read_live_context(r, "ACME")
    assert ctx["vwap"] == 110.5 and isinstance(ctx["vwap"], float)
    assert ctx["rsi_5m"] == 72.0
    assert ctx["trend_regime"] == "strong_uptrend"
    assert ctx["updated_at"] == "2025-06-02T10:00:00+05:30"


def test_empty_strings_become_none():
    r = FakeRedis()
    r.hset("ind:ACME", mapping={"vwap": "", "trend_regime": "", "rsi_5m": "55"})
    ctx = enrich.read_live_context(r, "ACME")
    assert ctx["vwap"] is None
    assert ctx["trend_regime"] is None
    assert ctx["rsi_5m"] == 55.0


def test_falls_back_to_sqlite_when_redis_cold(signals_db):
    signals_db.execute(
        "INSERT INTO indicator_live "
        "(symbol, updated_at, vwap, rsi_5m, trend_regime, momentum_state) "
        "VALUES ('ACME', '2025-06-02T10:00:00+05:30', 99.0, 60.0, 'uptrend', 'bullish')"
    )
    signals_db.commit()
    # Redis present but no ind:ACME hash -> SQLite fallback.
    ctx = enrich.read_live_context(FakeRedis(), "ACME", signals_db)
    assert ctx["vwap"] == 99.0
    assert ctx["trend_regime"] == "uptrend"
    assert ctx["momentum_state"] == "bullish"


def test_all_none_when_nothing_known(signals_db):
    ctx = enrich.read_live_context(FakeRedis(), "GHOST", signals_db)
    assert set(ctx) == set(enrich.CONTEXT_FIELDS)
    assert all(v is None for v in ctx.values())
