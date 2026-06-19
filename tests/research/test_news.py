"""Unit tests for the News Intelligence engine (Engine 6) classifier + scoring.
Deterministic — pins the event taxonomy and the routine-compliance guard that stops
'Certificate under SEBI…' from masquerading as a regulatory action.
"""
from __future__ import annotations

import sqlite3

from nse_data.research import news_engine as ne


def test_classify_positive_events():
    assert ne.classify("Bagging/Receiving of orders/contracts", "won a work order") == "order_win"
    assert ne.classify("Acquisition", "to acquire 100% stake") == "acquisition"
    assert ne.classify("Outcome of Board Meeting", "approved buyback of shares") == "buyback"
    assert ne.classify("Corporate Action", "bonus issue 1:1") == "bonus_split"
    assert ne.classify("Capacity expansion", "new plant commissioned") == "expansion"
    assert ne.classify("Dividend", "interim dividend declared") == "dividend"


def test_classify_negative_events():
    assert ne.classify("Change in Auditors", "resignation of auditor") == "auditor_exit"
    assert ne.classify("Resignation of Director/KMP/SMP", "CFO resigned") == "kmp_exit"
    assert ne.classify("SEBI order", "adjudication order passed against the company") == "regulatory"
    assert ne.classify("Penalty", "penalty of Rs 5 lakh imposed") == "penalty"
    assert ne.classify("Pledge", "promoter shares pledged") == "pledge"


def test_routine_compliance_is_ignored():
    # the false-positive we fixed: routine SEBI-regulations citations are NOT events
    assert ne.classify("Certificate under SEBI (Depositories and Participants) Regulations", "") is None
    assert ne.classify("Disclosure under SEBI Takeover Regulations", "") is None
    assert ne.classify("Submission under Regulation 74", "") is None


def test_sentiment_tiebreak_for_rating_and_news():
    assert ne.classify("Credit Rating", "downgraded", sentiment=-1) == "rating_down"
    assert ne.classify("Credit Rating", "reaffirmed", sentiment=1) == "rating_up"
    assert ne.classify("Press Release", "great quarter", sentiment=0.8) == "positive_news"
    assert ne.classify("General Update", "weak demand", sentiment=-0.8) == "negative_news"
    assert ne.classify("Press Release", "neutral note", sentiment=0.0) is None


def test_news_raw_scores_and_decays():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE raw_announcements(symbol TEXT, subject TEXT, details TEXT, "
              "sentiment, broadcast_epoch INT)")
    now = 1_770_000_000
    day = 86400
    # a fresh order win + an old (heavily decayed) one + a recent governance exit
    c.executemany("INSERT INTO raw_announcements VALUES ('X',?,?,?,?)", [
        ("New order win secures large contract", "big order", None, now - 2 * day),
        ("Bagging/Receiving of orders/contracts", "older order", None, now - 170 * day),
        ("Resignation of Director/KMP/SMP", "CFO exit", None, now - 5 * day),
    ])
    r = ne.news_raw(c, "X", now)
    assert r["news_score"] > 50          # positive flow lifts it
    assert r["news_risk"] < 100          # governance exit dents safety
    assert r["top_pos"][0] == "order_win"
    assert r["top_neg"][0] == "kmp_exit"
    # the fresh order (2d) must outweigh the 170-day-old one as the top positive
    assert "secures" in r["top_pos"][2].lower()


def test_news_raw_quiet_name_is_neutral():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE raw_announcements(symbol TEXT, subject TEXT, details TEXT, "
              "sentiment, broadcast_epoch INT)")
    r = ne.news_raw(c, "QUIET", 1_770_000_000)
    assert r["news_score"] == 50.0 and r["news_risk"] == 100.0 and r["n_events"] == 0
