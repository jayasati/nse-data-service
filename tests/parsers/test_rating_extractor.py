"""Tests for the Week-16 credit-rating extractor."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from nse_data.parsers import rating_extractor as rx

_IST = timezone(timedelta(hours=5, minutes=30))


# ---- pure parsing ----------------------------------------------------------

def test_extract_agency():
    assert rx.extract_agency("Rated by CRISIL Ratings Ltd") == "CRISIL"
    assert rx.extract_agency("India Ratings and Research") == "India Ratings"
    assert rx.extract_agency("no agency here") is None


def test_extract_action_definitive_only():
    # a grade-tied downgrade is a downgrade
    assert rx.extract_action("downgraded the NCD to BB from BBB-") == "downgrade"
    assert rx.extract_action("rating upgraded to AA- from A+") == "upgrade"
    assert rx.extract_action("placed on rating watch with negative implications") == "watch_negative"
    assert rx.extract_action("rating reaffirmed at AA") == "reaffirm"
    assert rx.extract_action("credit rating assigned") == "assigned"


def test_action_ignores_scenario_and_outlook_boilerplate():
    # the killer real case (INDUSINDBK): a reaffirmation whose text discusses
    # hypothetical up/downgrades and an OUTLOOK change must NOT read as a rating action
    txt = ("Long-term rating reaffirmed at Ba1. The outlook was upgraded from "
           "'Negative' to 'Stable'. The rating could be downgraded if asset "
           "quality deteriorates; it could be upgraded if the BCA improves.")
    assert rx.extract_action(txt) == "reaffirm"


def test_agency_word_boundary_and_global():
    assert rx.extract_agency("issued with due care by Moody's Investors Service") == "Moody's"
    assert rx.extract_agency("CARE Ratings Limited has ...") == "CARE"
    assert rx.extract_agency("exercised with due care") is None     # not 'CARE' agency


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
    assert rx.credit_signal_type("assigned", 0) == "credit_rating_assigned"
    assert rx.credit_signal_type("reaffirm", 0) is None     # reaffirm never alerts


# ---- multi-instrument headline ---------------------------------------------

def test_parse_filing_headline_and_scoring():
    txt = ("CRISIL Ratings Limited: Non-Convertible Debentures 30,980 "
           "CRISIL AAA/Stable Commercial Paper CRISIL A1+ Rating Outstanding")
    h = rx.parse_filing(txt)
    assert h["agencies"] == ["CRISIL"]
    assert h["worst_action"] == "reaffirm"               # 'Rating Outstanding'
    assert h["min_lt_grade"] == "AAA" and h["credit_quality_score"] == 100
    assert h["has_short_term"] == 1


def test_parse_filing_downgrade_to_junk():
    h = rx.parse_filing("ICRA downgraded the NCD from BBB-/Stable to BB/Negative")
    assert h["worst_action"] == "downgrade"
    assert h["min_lt_grade"] == "BB" and h["is_junk_downgrade"] == 1
    assert h["credit_quality_score"] == 40


def test_parse_filing_ignores_boilerplate_agencies():
    # 'S&P BSE Sensex' / 'a Fitch Group company' must NOT register as agencies
    txt = ("India Ratings & Research has reaffirmed IND AAA/Stable. India Ratings "
           "is a Fitch Group company. Returns benchmarked to the S&P BSE 500 index.")
    assert rx.extract_agencies(txt) == ["India Ratings"]


def test_multi_agency_detection():
    txt = "CRISIL AAA/Stable and ICRA reaffirmed [ICRA]AAA(Stable) for the issuer"
    assert set(rx.extract_agencies(txt)) == {"CRISIL", "ICRA"}


def test_rating_message_shape():
    h = {"worst_action": "downgrade", "agencies": ["CARE"], "n_instruments": 2,
         "min_lt_grade": "BB", "credit_quality_score": 40, "is_junk_downgrade": 1,
         "outlook_negative": 1, "has_short_term": 1,
         "lines": [{"instrument_type": "NCD", "lt_rating": "BB"}]}
    msg = rx.build_rating_message("ZED", h, "07-Jun-2026 18:30:00")
    assert "ZED — Credit DOWNGRADE" in msg
    assert "BB" in msg and "JUNK" in msg and "quality 40/100" in msg


# ---- DB pass + emit --------------------------------------------------------

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE raw_announcements (fingerprint TEXT PRIMARY KEY, symbol TEXT,
            subject TEXT, broadcast_dt TEXT, pdf_text TEXT);
        CREATE TABLE raw_rating_actions (id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, agency TEXT, action TEXT, old_rating TEXT, new_rating TEXT,
            instrument_type TEXT, is_junk_downgrade INTEGER DEFAULT 0,
            broadcast_dt TEXT, announcement_fingerprint TEXT UNIQUE,
            agencies TEXT, n_instruments INTEGER, worst_action TEXT,
            min_lt_grade TEXT, credit_quality_score REAL,
            outlook_negative INTEGER, has_short_term INTEGER);
        CREATE TABLE raw_rating_lines (id INTEGER PRIMARY KEY AUTOINCREMENT,
            announcement_fingerprint TEXT, symbol TEXT, agency TEXT,
            instrument_type TEXT, rated_amount TEXT, lt_rating TEXT, lt_outlook TEXT,
            st_rating TEXT, line_action TEXT, broadcast_dt TEXT);
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


# ---- credit → signal scoring -----------------------------------------------

def test_credit_adjustment_swing_vs_intraday():
    from nse_data.signals.confidence import _credit_adjustment
    # recent downgrade: penalises both, but standing grade only applies to swing
    dg = {"action": "downgrade", "days_since": 0, "quality_score": 40, "is_junk": 0,
          "st_stressed": True}
    assert _credit_adjustment(dg, is_intraday=False) == pytest.approx(-0.15 - 0.10 - 0.05)
    assert _credit_adjustment(dg, is_intraday=True) == pytest.approx(-0.15)
    # high-grade reaffirm: small standing nudge for swing, nothing intraday
    aaa = {"action": "reaffirm", "days_since": 30, "quality_score": 100, "is_junk": 0}
    assert _credit_adjustment(aaa, is_intraday=False) == pytest.approx(0.05)
    assert _credit_adjustment(aaa, is_intraday=True) == 0.0
    assert _credit_adjustment(None, is_intraday=False) == 0.0


def test_credit_event_window_intraday_decays_fast():
    from nse_data.signals.confidence import _credit_adjustment
    # a 3-day-old downgrade: still in swing window, but past the intraday window
    old_dg = {"action": "downgrade", "days_since": 3, "quality_score": 60, "is_junk": 0}
    assert _credit_adjustment(old_dg, is_intraday=False) == pytest.approx(-0.15)
    assert _credit_adjustment(old_dg, is_intraday=True) == 0.0


def test_is_junk_downgrade_kill():
    assert rx.is_junk_downgrade_kill(
        {"is_junk": 1, "action": "downgrade", "days_since": 2}) is True
    assert rx.is_junk_downgrade_kill(
        {"is_junk": 1, "action": "downgrade", "days_since": 10}) is False   # too old
    assert rx.is_junk_downgrade_kill(
        {"is_junk": 0, "action": "downgrade", "days_since": 1}) is False     # not junk
    assert rx.is_junk_downgrade_kill(None) is False


def test_latest_credit_by_symbol_tolerates_missing_tables():
    assert rx.latest_credit_by_symbol(
        sqlite3.connect(":memory:"),
        now=datetime(2026, 6, 8, tzinfo=_IST)) == {}
