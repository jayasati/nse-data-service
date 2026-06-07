"""Tests for the Week-16 credit-rating extractor."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from nse_data.parsers import rating_extractor as rx

_IST = timezone(timedelta(hours=5, minutes=30))


# ---- pure parsing ----------------------------------------------------------

def test_extract_agency():
    assert rx.extract_agency("Rated by CRISIL Ratings Ltd") == "CRISIL"
    assert rx.extract_agency("India Ratings and Research") == "India Ratings"
    assert rx.extract_agency("no agency here") is None


def test_extract_action_worst_first():
    assert rx.extract_action("the rating was downgraded and reaffirmed") == "downgrade"
    assert rx.extract_action("rating upgraded") == "upgrade"
    assert rx.extract_action("placed on rating watch with negative implications") == "watch_negative"
    assert rx.extract_action("rating assigned") == "assigned"
    assert rx.extract_action("rating reaffirmed") == "reaffirm"


def test_extract_ratings_skips_agency_prefix():
    old, new = rx.extract_ratings("downgraded from CRISIL A-/Stable to CRISIL BBB+/Negative")
    assert old == "A-" and new == "BBB+"


def test_is_junk_downgrade():
    assert rx.is_junk_downgrade("BB+") is True
    assert rx.is_junk_downgrade("B") is True
    assert rx.is_junk_downgrade("BBB-") is False
    assert rx.is_junk_downgrade(None) is False


def test_parse_rating_full():
    r = rx.parse_rating("ICRA downgraded from BBB-/Stable to BB/Negative for the NCD")
    assert r["agency"] == "ICRA" and r["action"] == "downgrade"
    assert r["new_rating"] == "BB" and r["is_junk_downgrade"] == 1
    assert r["instrument_type"] == "NCD"


def test_credit_signal_type():
    assert rx.credit_signal_type("downgrade", 1) == "credit_downgrade_junk"
    assert rx.credit_signal_type("downgrade", 0) == "credit_downgrade"
    assert rx.credit_signal_type("upgrade", 0) == "credit_upgrade"
    assert rx.credit_signal_type("watch_negative", 0) == "credit_watch_negative"
    assert rx.credit_signal_type("reaffirm", 0) is None


def test_rating_message_shape():
    r = {"agency": "CARE", "action": "downgrade", "old_rating": "A",
         "new_rating": "BB", "instrument_type": "Long Term", "is_junk_downgrade": 1}
    msg = rx.build_rating_message("ZED", r, "07-Jun-2026 18:30:00")
    assert "ZED — Credit Downgrade" in msg
    assert "A → BB" in msg and "junk territory" in msg


# ---- DB pass + emit --------------------------------------------------------

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE raw_announcements (fingerprint TEXT PRIMARY KEY, symbol TEXT,
            subject TEXT, broadcast_dt TEXT, pdf_text TEXT);
        CREATE TABLE raw_rating_actions (id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, agency TEXT, action TEXT, old_rating TEXT, new_rating TEXT,
            instrument_type TEXT, is_junk_downgrade INTEGER DEFAULT 0,
            broadcast_dt TEXT, announcement_fingerprint TEXT UNIQUE);
        CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,
            signal_type TEXT, detected_at TEXT, price REAL, oi_change_pct REAL,
            price_change_pct REAL, volume_ratio REAL);
    """)
    return conn


_TEXT = "CRISIL downgraded from CRISIL A-/Stable to CRISIL BB/Negative for the NCD" + " x" * 60


def test_backfill_inserts_without_alerting():
    conn = _db()
    conn.execute("INSERT INTO raw_announcements VALUES "
                 "('fp1','ZED','Credit Rating- Revision','01-Jan-2025 18:00:00', ?)", (_TEXT,))
    conn.commit()
    sent = []
    rep = rx.run_rating_extraction(conn, emit=True, sender=lambda *a: sent.append(a) or True,
                                   now=datetime(2026, 6, 7, 20, 0, tzinfo=_IST))
    assert rep["inserted"] == 1 and rep["signaled"] == 0     # old → no alert
    assert sent == []


def test_recent_downgrade_alerts_and_signals():
    conn = _db()
    conn.execute("INSERT INTO raw_announcements VALUES "
                 "('fp2','ZED','Credit Rating- Revision','06-Jun-2026 18:00:00', ?)", (_TEXT,))
    conn.commit()
    sent = []
    rep = rx.run_rating_extraction(conn, emit=True, sender=lambda *a: sent.append(a) or True,
                                   now=datetime(2026, 6, 7, 20, 0, tzinfo=_IST))
    assert rep["inserted"] == 1 and rep["signaled"] == 1
    assert len(sent) == 1
    sig = conn.execute("SELECT signal_type FROM signals WHERE symbol='ZED'").fetchone()
    assert sig[0] == "credit_downgrade_junk"


def test_idempotent_rerun():
    conn = _db()
    conn.execute("INSERT INTO raw_announcements VALUES "
                 "('fp3','ZED','Credit Rating','06-Jun-2026 18:00:00', ?)", (_TEXT,))
    conn.commit()
    now = datetime(2026, 6, 7, 20, 0, tzinfo=_IST)
    rx.run_rating_extraction(conn, emit=False, now=now)
    rep2 = rx.run_rating_extraction(conn, emit=True, sender=lambda *a: True, now=now)
    assert rep2["inserted"] == 0          # already present → no duplicate, no alert


def test_form_template_not_a_false_downgrade():
    # NSE structured form: the parenthesised list is a LABEL; actual action is 'Assigned'
    text = "Rating Action (New/ Upgrade/ Downgrade/ Re- Affirm/ Other) Assigned by ICRA"
    assert rx.extract_action(text) == "assigned"          # not 'downgrade'


def test_new_first_downgrade_phrasing():
    text = "Long Term Rating Crisil BB/Negative (Downgraded from 'Crisil BBB-/Stable')"
    r = rx.parse_rating(text)
    assert r["action"] == "downgrade"
    assert r["old_rating"] == "BBB-" and r["new_rating"] == "BB"
    assert r["is_junk_downgrade"] == 1


def test_no_phantom_grade_from_prose():
    # an upgrade narrative with a stray 'from … to …' must not invent an old grade
    text = "India Ratings has upgraded the long-term rating to 'IND AA-' with a Stable outlook"
    r = rx.parse_rating(text)
    assert r["action"] == "upgrade" and r["new_rating"] == "AA-"
