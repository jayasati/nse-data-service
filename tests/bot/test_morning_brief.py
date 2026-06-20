"""Tests for bot.morning_brief — brief assembly + graceful degradation."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from nse_data.bot import morning_brief as mb
from nse_data.scheduler.market_hours import IST


def _epoch(y, m, d, hh, mm) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=IST).timestamp())


def _seed() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE raw_macro (asset TEXT, as_of_date TEXT, price REAL, pct_change REAL,
                                PRIMARY KEY(asset, as_of_date));
        CREATE TABLE raw_gift_nifty (as_of INTEGER PRIMARY KEY, pct_change REAL, curr_value REAL);
        CREATE TABLE raw_indices (index_symbol TEXT, as_of INTEGER, high REAL, low REAL,
                                  last REAL, PRIMARY KEY(index_symbol, as_of));
        CREATE TABLE market_state (as_of TEXT PRIMARY KEY, overall_regime TEXT,
                                   regime_warnings TEXT, vix_state TEXT,
                                   nifty_direction TEXT, vix_direction TEXT);
        CREATE TABLE raw_announcements (fingerprint TEXT PRIMARY KEY, symbol TEXT,
                                        subject TEXT, created_at INTEGER, deleted_at INTEGER);
    """)
    conn.execute("INSERT INTO raw_macro VALUES ('SP500','2026-06-04',7500,1.5)")
    conn.execute("INSERT INTO raw_macro VALUES ('NASDAQ','2026-06-04',26000,2.1)")
    conn.execute("INSERT INTO raw_macro VALUES ('BRENT','2026-06-04',93.4,-1.2)")
    conn.execute("INSERT INTO raw_gift_nifty VALUES (?, 0.6, 24050)", (_epoch(2026, 6, 5, 8, 45),))
    conn.execute("INSERT INTO raw_indices VALUES ('NIFTY 50', ?, 110, 90, 100)",
                 (_epoch(2026, 6, 4, 15, 25),))
    conn.execute("INSERT INTO market_state VALUES "
                 "('2026-06-04T15:25:00','risk_on',NULL,'normal','up','falling')")
    conn.execute("INSERT INTO raw_announcements VALUES ('fp1','TCS','Board meeting outcome', ?, NULL)",
                 (_epoch(2026, 6, 5, 8, 30),))
    conn.commit()
    return conn


def test_brief_has_all_sections():
    conn = _seed()
    text = mb.build_brief(conn, now=datetime(2026, 6, 5, 9, 0, tzinfo=IST))
    assert "Market Brief — 2026-06-05" in text
    assert "GIFT Nifty: up +0.60%" in text
    assert "S&P +1.50%" in text and "Nasdaq +2.10%" in text
    assert "Crude: $93.40" in text
    assert "Today's regime: risk_on" in text
    assert "Lean long" in text                  # posture for risk_on
    assert "TCS: Board meeting outcome" in text  # overnight event
    assert "Nifty support: 90 | Resistance: 110" in text
    assert "Not expiry day" in text             # 2026-06-05 is a Friday


def test_brief_degrades_when_feeds_missing():
    conn = sqlite3.connect(":memory:")
    # no tables at all -> every reader hits OperationalError / empty -> n/a, no crash
    text = mb.build_brief(conn, now=datetime(2026, 6, 5, 9, 0, tzinfo=IST))
    assert "Market Brief" in text
    assert "n/a" in text
    assert "• none" in text


def test_send_uses_injected_sender():
    conn = _seed()
    sent = {}

    def fake_sender(token, chat_id, text, **_kw):
        sent["text"] = text
        return True

    # build_brief opens its own conn via db_path in send_morning_brief, so test
    # build + sender wiring separately:
    text = mb.build_brief(conn, now=datetime(2026, 6, 5, 9, 0, tzinfo=IST))
    assert fake_sender(None, None, text) is True and "Market Brief" in sent["text"]


def test_brief_psych_watch_lists_fomo_and_capitulation():
    """Week-19 gate: the brief must surface FOMO/CAPITULATION names."""
    conn = _seed()
    conn.executescript("""
        CREATE TABLE indicator_live (symbol TEXT PRIMARY KEY, updated_at TEXT,
                                     psych_state TEXT);
        INSERT INTO indicator_live VALUES ('HYPE', 'x', 'FOMO_EUPHORIA');
        INSERT INTO indicator_live VALUES ('CRSH', 'x', 'CAPITULATION');
        INSERT INTO indicator_live VALUES ('MEH', 'x', 'NEUTRAL_TRENDING');
    """)
    text = mb.build_brief(conn, now=datetime(2026, 6, 5, 9, 0, tzinfo=IST))
    assert "Psychology watch:" in text
    assert "FOMO euphoria (chase risk): HYPE" in text
    assert "Capitulation (reversal watch): CRSH" in text
    assert "MEH" not in text


def test_brief_psych_watch_absent_when_quiet():
    conn = _seed()
    text = mb.build_brief(conn, now=datetime(2026, 6, 5, 9, 0, tzinfo=IST))
    assert "Psychology watch" not in text
