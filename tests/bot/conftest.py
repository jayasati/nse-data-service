"""Dispatcher fixtures — minimal DB + Redis double for bot tests."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from nse_data.scheduler.market_hours import IST

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

_MIGRATIONS = (
    "003_bhavcopy.sql",       # raw_bhavcopy_cm (listing-age gate)
    "018_price_bands.sql",    # raw_price_bands (T2T gate)
    "035_indicator_live.sql", # indicator_live (enrich fallback)
    "036_signals.sql",        # signals, signal_features
    "049_signal_fake_breakout.sql",   # signals.fake_breakout_risk
)

NOW = datetime(2025, 6, 2, 10, 0, 0, tzinfo=IST)


class FakeRedis:
    """Just the reads the dispatcher path uses: smembers, get, hgetall."""

    def __init__(self) -> None:
        self.sets: dict[str, set] = {}
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict] = {}

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def get(self, key):
        return self.strings.get(key)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))


@pytest.fixture
def bot_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for m in _MIGRATIONS:
        conn.executescript((MIGRATIONS_DIR / m).read_text())
    conn.commit()
    return conn


def seed_signal(conn, *, symbol="ACME", detected_at=None, volume_ratio=2.0,
                price=100.0, atr=2.0, oi=5.0, pchg=1.5) -> int:
    detected_at = detected_at or NOW.isoformat()
    cur = conn.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at, price, "
        "oi_change_pct, price_change_pct, volume_ratio) "
        "VALUES (?, 'long_buildup', ?, ?, ?, ?, ?)",
        (symbol, detected_at, price, oi, pchg, volume_ratio),
    )
    sid = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO signal_features (signal_id, symbol, detected_at, atr_14_daily) "
        "VALUES (?, ?, ?, ?)",
        (sid, symbol, detected_at, atr),
    )
    # Enough daily history to clear the newly-listed gate.
    for i in range(35):
        conn.execute(
            "INSERT INTO raw_bhavcopy_cm (date, symbol, series, close, volume) "
            "VALUES (?, ?, 'EQ', 100, 1000)",
            (f"2025-04-{i+1:02d}" if i < 30 else f"2025-05-{i-29:02d}", symbol),
        )
    conn.commit()
    return sid


def set_high_confidence(redis, symbol="ACME"):
    """ind:{symbol} hash that scores well above the 0.65 threshold."""
    redis.hashes[f"ind:{symbol}"] = {
        "price_vs_vwap": "above", "vwap_slope": "0.5",
        "rsi_5m": "60.0", "trend_regime": "strong_uptrend",
    }
