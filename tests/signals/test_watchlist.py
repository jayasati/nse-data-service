"""Tests for the dynamic live watchlist (Phase 4)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from nse_data.indicators import universe
from nse_data.signals import watchlist

_IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 6, 5, 10, 0, tzinfo=_IST)   # a Friday


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE live_watchlist (symbol TEXT PRIMARY KEY, reason TEXT,
            added_at TEXT, expires_at TEXT);
        CREATE TABLE raw_announcements (fingerprint TEXT PRIMARY KEY, symbol TEXT,
            subject TEXT, priority TEXT, sentiment TEXT, created_at INTEGER);
        CREATE TABLE raw_oi_spurts (symbol TEXT, as_of INTEGER);
        CREATE TABLE raw_high_low_52w (symbol TEXT, as_of INTEGER, event TEXT,
            price_tier TEXT);
    """)
    return conn


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def test_refresh_populates_by_trigger():
    conn = _db()
    recent = _epoch(NOW - timedelta(hours=2))
    conn.execute("INSERT INTO raw_announcements VALUES ('a','RATECO','Credit Rating revised','low',NULL,?)", (recent,))
    conn.execute("INSERT INTO raw_announcements VALUES ('b','NEWSCO','Board outcome','high',NULL,?)", (recent,))
    conn.execute("INSERT INTO raw_oi_spurts VALUES ('OICO', ?)", (recent,))
    conn.execute("INSERT INTO raw_high_low_52w VALUES ('BRKCO', ?, 'high', 'gt500')", (recent,))
    conn.execute("INSERT INTO raw_high_low_52w VALUES ('PENNY', ?, 'high', 'lte20')", (recent,))  # excluded
    conn.commit()

    counts = watchlist.refresh_watchlist(conn, now=NOW)
    active = universe.active_watchlist(conn, NOW.isoformat())
    assert active == {"RATECO", "NEWSCO", "OICO", "BRKCO"}     # PENNY excluded
    assert counts["rating"] == 1 and counts["news"] == 1
    assert counts["oi_spurt"] == 1 and counts["breakout_52wh"] == 1


def test_old_triggers_ignored_and_expired_pruned():
    conn = _db()
    stale = _epoch(NOW - timedelta(days=10))     # outside the 30h lookback
    conn.execute("INSERT INTO raw_oi_spurts VALUES ('OLDCO', ?)", (stale,))
    # an already-expired manual row should be pruned
    conn.execute("INSERT INTO live_watchlist VALUES ('GONE','news','x', ?)",
                 ((NOW - timedelta(days=1)).isoformat(),))
    conn.commit()

    watchlist.refresh_watchlist(conn, now=NOW)
    assert universe.active_watchlist(conn, NOW.isoformat()) == set()


def test_retrigger_extends_expiry():
    conn = _db()
    early = (NOW - timedelta(days=2)).isoformat()
    watchlist.add_to_watchlist(conn, "X", "news", early, (NOW + timedelta(days=1)).isoformat())
    later = (NOW + timedelta(days=9)).isoformat()
    watchlist.add_to_watchlist(conn, "X", "rating", NOW.isoformat(), later)
    conn.commit()
    row = conn.execute("SELECT reason, expires_at FROM live_watchlist WHERE symbol='X'").fetchone()
    assert row[0] == "rating"          # newest reason
    assert row[1] == later             # expiry extended, not shortened


def test_live_universe_is_core_plus_watchlist():
    conn = _db()
    conn.executescript("""
        CREATE TABLE raw_fno_list (symbol TEXT);
        CREATE TABLE raw_bhavcopy_cm (date TEXT, symbol TEXT, series TEXT, turnover_lacs REAL);
        INSERT INTO raw_fno_list VALUES ('AAA'),('BBB'),('CCC');
        INSERT INTO raw_bhavcopy_cm VALUES ('2026-06-05','AAA','EQ',900),
            ('2026-06-05','BBB','EQ',500),('2026-06-05','CCC','EQ',100);
    """)
    # watchlist adds a non-core name
    watchlist.add_to_watchlist(conn, "WATCHED", "news", NOW.isoformat(),
                               (NOW + timedelta(days=3)).isoformat())
    conn.commit()
    core = universe.top_fno_by_value(conn, limit=2)
    assert core == ["AAA", "BBB"]      # ranked by turnover, top 2
    live = universe.live_universe(conn, core_size=2)
    assert "WATCHED" in live and "AAA" in live and "BBB" in live
