"""Feature snapshotting (signals/feature_store.py)."""

from __future__ import annotations

from nse_data.signals import feature_store


def _make_signal(conn, symbol="ACME") -> int:
    cur = conn.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at) VALUES (?, 'long_buildup', ?)",
        (symbol, "2025-06-02T10:00:00+05:30"),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_snapshot_writes_all_columns(signals_db):
    sid = _make_signal(signals_db)
    ctx = {
        "vwap": 110.5, "vwap_slope": 1.0, "rsi_5m": 72.0,
        "trend_regime": "strong_uptrend", "momentum_state": "overbought",
        "atr_14_daily": 2.0,
    }
    feature_store.snapshot_features(
        signals_db, sid, "ACME", "2025-06-02T10:00:00+05:30", ctx,
        volume_ratio=1.8, market_regime="risk_on",
    )
    row = signals_db.execute(
        "SELECT symbol, vwap, rsi_5m, trend_regime, atr_14_daily, volume_ratio, "
        "market_regime FROM signal_features WHERE signal_id = ?", (sid,),
    ).fetchone()
    assert row == ("ACME", 110.5, 72.0, "strong_uptrend", 2.0, 1.8, "risk_on")


def test_missing_context_keys_snapshot_as_null(signals_db):
    sid = _make_signal(signals_db)
    feature_store.snapshot_features(
        signals_db, sid, "ACME", "2025-06-02T10:00:00+05:30", {},
    )
    row = signals_db.execute(
        "SELECT vwap, trend_regime, volume_ratio, market_regime "
        "FROM signal_features WHERE signal_id = ?", (sid,),
    ).fetchone()
    assert row == (None, None, None, None)


def test_snapshot_is_idempotent_on_signal_id(signals_db):
    sid = _make_signal(signals_db)
    feature_store.snapshot_features(signals_db, sid, "ACME", "t", {"vwap": 1.0})
    feature_store.snapshot_features(signals_db, sid, "ACME", "t", {"vwap": 2.0})
    rows = signals_db.execute(
        "SELECT vwap, COUNT(*) FROM signal_features WHERE signal_id = ?", (sid,),
    ).fetchone()
    assert rows == (2.0, 1)   # overwritten, not duplicated
