"""
Signal-engine fixtures.

An in-memory SQLite carrying every raw + indicator table the detector reads,
plus a small Redis double (set/NX/EX, sets, hashes) and seed helpers tuned so a
test can stand up a clean "this should fire" setup in a few lines.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from nse_data.scheduler.market_hours import IST

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

# Everything the detector + its compute/enrich helpers touch.
_MIGRATIONS = (
    "002_snapshots.sql",        # raw_oi_spurts
    "003_bhavcopy.sql",         # raw_bhavcopy_cm
    "006_phase7_day1.sql",      # raw_equity_quotes
    "007_phase7_day2.sql",      # raw_high_low_52w
    "010_phase7_day5.sql",      # raw_fno_list, raw_index_members
    "018_price_bands.sql",      # raw_price_bands
    "025_intraday_candles.sql", # raw_intraday_candles
    "026_indicator_sma.sql",
    "027_indicator_rsi.sql",
    "028_indicator_macd.sql",
    "029_indicator_rsi_5m.sql",
    "030_indicator_macd_5m.sql",
    "031_drop_redundant_intraday_indexes.sql",
    "034_indicator_vwap_5m.sql",
    "035_indicator_live.sql",   # indicator_live (enrich fallback)
    "036_signals.sql",          # signals, signal_features, ...
    "049_signal_fake_breakout.sql",   # signals.fake_breakout_risk
    "053_signal_horizon.sql",         # signals.horizon
)

# A fixed mid-morning instant outside the opening (≤09:30) and lunch
# (11:30–13:30) skip windows. 2025-06-02 is a Monday and not a holiday.
NOW = datetime(2025, 6, 2, 10, 0, 0, tzinfo=IST)
SESSION_START = int(NOW.replace(hour=9, minute=15, second=0, microsecond=0).timestamp())


class FakeRedis:
    """In-memory Redis double covering only what the signal engine calls."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.sets: dict[str, set] = {}
        self.hashes: dict[str, dict] = {}
        self.ttls: dict[str, int] = {}

    # --- string ops (dedup uses SET NX EX) ---
    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def get(self, key):
        return self.strings.get(key)

    # --- set ops (blacklist) ---
    def sadd(self, key, *vals):
        self.sets.setdefault(key, set()).update(vals)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    # --- hash ops (ind:{symbol}) ---
    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def expire(self, key, ttl):
        self.ttls[key] = ttl

    def delete(self, key):
        self.strings.pop(key, None)
        self.sets.pop(key, None)
        self.hashes.pop(key, None)
        self.ttls.pop(key, None)

    def ttl(self, key):
        return self.ttls.get(key, -1)

    def pipeline(self):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, store: "FakeRedis") -> None:
        self._store = store
        self._ops: list = []

    def sadd(self, key, *vals):
        self._ops.append(lambda: self._store.sadd(key, *vals)); return self

    def hset(self, key, mapping):
        self._ops.append(lambda: self._store.hset(key, mapping=mapping)); return self

    def expire(self, key, ttl):
        self._ops.append(lambda: self._store.expire(key, ttl)); return self

    def delete(self, key):
        self._ops.append(lambda: self._store.delete(key)); return self

    def execute(self):
        for op in self._ops:
            op()
        self._ops.clear()


@pytest.fixture
def signals_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for m in _MIGRATIONS:
        conn.executescript((MIGRATIONS_DIR / m).read_text())
    conn.commit()
    return conn


# ------------------------------------------------------------------- seeds

def seed_universe(conn: sqlite3.Connection, *symbols: str) -> None:
    """Put symbols in the F&O list so fno_plus_nifty500 returns them."""
    for s in symbols:
        conn.execute(
            "INSERT INTO raw_fno_list (symbol, fetched_at) VALUES (?, 0)", (s,)
        )
    conn.commit()


def seed_oi_spurt(conn, symbol, *, latest_oi, prev_oi, as_of=SESSION_START) -> None:
    conn.execute(
        "INSERT INTO raw_oi_spurts "
        "(symbol, as_of, latest_oi, prev_oi, change_in_oi, avg_oi_pct, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (symbol, as_of, latest_oi, prev_oi, latest_oi - prev_oi,
         ((latest_oi - prev_oi) / prev_oi * 100.0) if prev_oi else None, 0),
    )
    conn.commit()


def seed_quote(conn, symbol, *, last_price, pct_change, as_of=SESSION_START,
               index_name="NIFTY 50") -> None:
    conn.execute(
        "INSERT INTO raw_equity_quotes "
        "(symbol, as_of, index_name, last_price, pct_change) VALUES (?, ?, ?, ?, ?)",
        (symbol, as_of, index_name, last_price, pct_change),
    )
    conn.commit()


def seed_bhavcopy(conn, symbol, *, days=20, volume=75_000, close=100.0,
                  start_date="2025-05-01", series="EQ") -> None:
    from datetime import date, timedelta
    base = date.fromisoformat(start_date)
    for i in range(days):
        d = (base + timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO raw_bhavcopy_cm "
            "(date, symbol, series, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (d, symbol, series, close, close, close, close, volume),
        )
    conn.commit()


def seed_intraday(conn, symbol, *, bars, volume, end_ts, bar_seconds=60) -> None:
    """Seed `bars` consecutive 1-min candles ending at `end_ts` (inclusive)."""
    for i in range(bars):
        ts = end_ts - (bars - 1 - i) * bar_seconds
        conn.execute(
            "INSERT INTO raw_intraday_candles "
            "(symbol, interval, ts, open, high, low, close, volume) "
            "VALUES (?, 'minute', ?, 100, 100, 100, 100, ?)",
            (symbol, ts, volume),
        )
    conn.commit()


def seed_52w_high(conn, symbol, *, as_of=SESSION_START, level=200.0) -> None:
    conn.execute(
        "INSERT INTO raw_high_low_52w "
        "(symbol, as_of, event, price_tier, new_52w_level) "
        "VALUES (?, ?, 'high', 'gt20', ?)",
        (symbol, as_of, level),
    )
    conn.commit()


def seed_price_band(conn, symbol, *, series="EQ", band=None) -> None:
    conn.execute(
        "INSERT INTO raw_price_bands (symbol, series, band) VALUES (?, ?, ?)",
        (symbol, series, band),
    )
    conn.commit()
