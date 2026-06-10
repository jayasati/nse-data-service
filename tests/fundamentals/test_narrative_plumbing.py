"""P7 plumbing end-to-end (playbook §4) — offline, no LLM/PDF.

The narrative path: filing text (raw_announcements.pdf_text) →
``narrative_for_fingerprint`` → ``narrative_json`` on extracted_financials →
``_detect_result_quality`` folds it into the sector verdict → the alert card
shows the 📰 line. Exercised on the IT print that turns on guidance: a flat
P&L quarter (reads neutral alone) with a guidance cut in the narrative must
fire ``result_quality_low`` / SHORT — the case the P&L-only engine missed.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import time

import pytest

from nse_data.bot.result_quality_message import format_result_quality
from nse_data.fundamentals import from_results as fr
from nse_data.signals import compute, detect
from nse_data.signals.dedup import SignalDedup
from nse_data.storage.db import apply_migrations

# A flat INFY quarter: P&L alone is neutral; the narrative decides.
FLAT_GROWTH = {"yoy_pat_pct": 3.0, "yoy_ebitda_pct": 0.5, "yoy_revenue_pct": 4.0}
CUT_NARRATIVE = {"guidance": "cut", "volume_growth": None, "order_inflow": None,
                 "fda_status": None, "dividend": None, "mgmt_tone": "negative"}

PRESS_TEXT = (
    "Outcome of Board Meeting. Given continued demand uncertainty in key "
    "markets, the company lowered its FY27 revenue growth guidance to 1%-2% "
    "in constant currency. The Board declared an interim dividend of ₹21 per "
    "equity share."
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c, "migrations")
    yield c
    c.close()


def _seed(conn, *, extracted_at: int, broadcast_dt: str, narrative: dict | None):
    fr.persist_extraction(
        conn, symbol="INFY", period_ending="2026-03-31", scope="standalone",
        fields={"revenue_cr": 37000.0, "pat_cr": 7800.0},
        units_phrase="INR crore", confidence=0.9, strategy="vision",
        source_fingerprint="infy-q4fy26", broadcast_dt=broadcast_dt,
        growth=FLAT_GROWTH, narrative=narrative, now=extracted_at,
    )


# ------------------------------------------------- text → NarrativeFields dict

def test_narrative_for_fingerprint_reads_pdf_text(conn):
    conn.execute(
        "INSERT INTO raw_announcements (fingerprint, segment, symbol, subject, "
        "broadcast_dt, pdf_text, created_at) VALUES ('fp1', 'EQ', 'INFY', "
        "'Financial Results', '10-Jun-2026 14:01:00', ?, 0)",
        (PRESS_TEXT,),
    )
    conn.commit()
    n = fr.narrative_for_fingerprint(conn, "fp1")
    assert n is not None
    assert n["guidance"] == "cut"
    assert n["dividend"] == 21.0


def test_narrative_for_fingerprint_none_when_no_signal_or_row(conn):
    conn.execute(
        "INSERT INTO raw_announcements (fingerprint, segment, symbol, subject, "
        "broadcast_dt, pdf_text, created_at) VALUES ('fp2', 'EQ', 'INFY', "
        "'Financial Results', '10-Jun-2026 14:01:00', 'Revenue grew during the quarter.', 0)",
    )
    conn.commit()
    assert fr.narrative_for_fingerprint(conn, "fp2") is None   # text, no signals
    assert fr.narrative_for_fingerprint(conn, "missing") is None
    assert fr.narrative_for_fingerprint(conn, None) is None


# ------------------------------------------------- persist → narrative_json

def test_persist_roundtrip_with_narrative_json(conn):
    _seed(conn, extracted_at=int(time.time()), broadcast_dt="10-Jun-2026 14:01:00",
          narrative=CUT_NARRATIVE)
    row = conn.execute(
        "SELECT narrative_json FROM extracted_financials WHERE symbol='INFY'"
    ).fetchone()
    assert json.loads(row[0])["guidance"] == "cut"


# ------------------------------------------------- detector folds the narrative

def _fire(monkeypatch):
    now = detect.now_ist()
    filed = (now - datetime.timedelta(minutes=10)).strftime("%d-%b-%Y %H:%M:%S")
    calls = []
    monkeypatch.setattr(detect, "_emit",
                        lambda *a, **k: calls.append((k["symbol"], k["signal_type"],
                                                      k["metrics"]["direction"],
                                                      k["metrics"]["quality_flags"])) or 1)
    monkeypatch.setattr(detect, "_load_listing_bars", lambda c: {"INFY": 999})
    monkeypatch.setattr(compute, "compute_price_change", lambda c, s: (None, 1620.0))
    return now, filed, calls


def test_guidance_cut_fires_short_where_pnl_alone_is_silent(conn, monkeypatch):
    now, filed, calls = _fire(monkeypatch)
    _seed(conn, extracted_at=int(now.timestamp()) - 120, broadcast_dt=filed,
          narrative=CUT_NARRATIVE)
    fired = detect._detect_result_quality(conn, None, SignalDedup(None), now.isoformat(), None, now)
    assert fired == 1
    symbol, signal_type, direction, flags = calls[0]
    assert (symbol, signal_type, direction) == ("INFY", "result_quality_low", "short")
    assert "guidance_cut" in flags


def test_same_pnl_without_narrative_stays_silent(conn, monkeypatch):
    now, filed, calls = _fire(monkeypatch)
    _seed(conn, extracted_at=int(now.timestamp()) - 120, broadcast_dt=filed, narrative=None)
    fired = detect._detect_result_quality(conn, None, SignalDedup(None), now.isoformat(), None, now)
    assert fired == 0 and not calls


# ------------------------------------- sibling discovery + merge (N1, LLM-off)

def _ins_ann(conn, fp, subject, broadcast_dt, pdf_text, pdf_path=None, symbol="INFY"):
    conn.execute(
        "INSERT INTO raw_announcements (fingerprint, segment, symbol, subject, "
        "broadcast_dt, pdf_text, pdf_path, created_at) VALUES (?, 'EQ', ?, ?, ?, ?, ?, 0)",
        (fp, symbol, subject, broadcast_dt, pdf_text, pdf_path),
    )
    conn.commit()


RESULT_TEXT = "Statement of unaudited financial results for the quarter."  # no signals
PR_TEXT = (
    "Press Release: the company lowered its FY27 revenue growth guidance to 1%-2% "
    "in constant currency. Large deal TCV of $3.1 billion. Attrition (LTM) was at 13.4%."
)


def test_sibling_press_release_merged_into_narrative(conn):
    _ins_ann(conn, "fp-res", "Financial Results", "10-Jun-2026 14:01:00", RESULT_TEXT)
    _ins_ann(conn, "fp-pr", "Press Release", "10-Jun-2026 14:35:00", PR_TEXT)
    n = fr.narrative_for_filing(conn, symbol="INFY", fingerprint="fp-res", use_llm=False)
    assert n is not None
    assert n["guidance"] == "cut"            # came from the sibling, not the result PDF
    assert n["tcv_usd_mn"] == 3100.0
    assert n["attrition_pct"] == 13.4
    assert n["_sources"] == ["Press Release"]
    assert sorted(n["_source_fps"]) == ["fp-pr", "fp-res"]


def test_press_release_outranks_result_pdf_on_conflict(conn):
    _ins_ann(conn, "fp-res", "Financial Results", "10-Jun-2026 14:01:00",
             "The company maintained its margin guidance of 20%-22%.")
    _ins_ann(conn, "fp-pr", "Press Release", "10-Jun-2026 14:35:00", PR_TEXT)
    n = fr.narrative_for_filing(conn, symbol="INFY", fingerprint="fp-res", use_llm=False)
    assert n["guidance"] == "cut"            # press release wins the field
    assert "result PDF" in n["_sources"]     # but the result PDF still contributed


def test_next_day_press_release_is_outside_window(conn):
    _ins_ann(conn, "fp-res", "Financial Results", "10-Jun-2026 14:01:00", RESULT_TEXT)
    _ins_ann(conn, "fp-pr", "Press Release", "11-Jun-2026 09:00:00", PR_TEXT)
    n = fr.narrative_for_filing(conn, symbol="INFY", fingerprint="fp-res", use_llm=False)
    assert n is None                          # result text alone carries no signals


def test_image_only_deck_goes_through_vision(conn, monkeypatch, tmp_path):
    deck = tmp_path / "deck.pdf"
    deck.write_bytes(b"%PDF-fake-deck")
    _ins_ann(conn, "fp-res", "Financial Results", "10-Jun-2026 14:01:00", RESULT_TEXT)
    _ins_ann(conn, "fp-deck", "Investor Presentation", "10-Jun-2026 15:00:00",
             None, pdf_path=str(deck))
    import nse_data.parsers.narrative as narrative_pkg
    monkeypatch.setattr(narrative_pkg, "extract_narrative_vision",
                        lambda data, **kw: ({"cc_revenue_growth_pct": 2.4}, 0.02))
    n = fr.narrative_for_filing(conn, symbol="INFY", fingerprint="fp-res", use_llm=True)
    assert n is not None and n["cc_revenue_growth_pct"] == 2.4
    assert n["_sources"] == ["Investor Presentation"]
    # with the LLM off, the image-only deck is skipped entirely
    assert fr.narrative_for_filing(conn, symbol="INFY", fingerprint="fp-res", use_llm=False) is None


# ------------------------------------------------- late-sibling refresh (N2)

def test_refresh_folds_in_late_press_release_and_caches(conn):
    now = int(time.time())
    _ins_ann(conn, "fp-res", "Financial Results", "10-Jun-2026 14:01:00", RESULT_TEXT)
    # Result extracted BEFORE the press release landed → no narrative stored.
    fr.persist_extraction(
        conn, symbol="INFY", period_ending="2026-03-31", scope="standalone",
        fields={"revenue_cr": 37000.0, "pat_cr": 7800.0},
        units_phrase="INR crore", confidence=0.9, strategy="vision",
        source_fingerprint="fp-res", broadcast_dt="10-Jun-2026 14:01:00",
        growth=FLAT_GROWTH, narrative=None, now=now,
    )
    _ins_ann(conn, "fp-pr", "Press Release", "10-Jun-2026 14:35:00", PR_TEXT)

    report = fr.refresh_narratives(conn, use_llm=False, now=now)
    assert report == {"checked": 1, "updated": 1}
    row = conn.execute(
        "SELECT narrative_json FROM extracted_financials WHERE symbol='INFY'"
    ).fetchone()
    stored = json.loads(row[0])
    assert stored["guidance"] == "cut"

    # Second pass: source set unchanged → cached, no rewrite.
    assert fr.refresh_narratives(conn, use_llm=False, now=now) == {"checked": 1, "updated": 0}


def test_refresh_ignores_rows_outside_lookback(conn):
    old = int(time.time()) - 10 * 3600
    _ins_ann(conn, "fp-res", "Financial Results", "10-Jun-2026 14:01:00", RESULT_TEXT)
    fr.persist_extraction(
        conn, symbol="INFY", period_ending="2026-03-31", scope="standalone",
        fields={"pat_cr": 7800.0}, units_phrase=None, confidence=0.9, strategy="vision",
        source_fingerprint="fp-res", broadcast_dt="10-Jun-2026 14:01:00",
        growth=FLAT_GROWTH, narrative=None, now=old,
    )
    _ins_ann(conn, "fp-pr", "Press Release", "10-Jun-2026 14:35:00", PR_TEXT)
    assert fr.refresh_narratives(conn, use_llm=False) == {"checked": 0, "updated": 0}


# ------------------------------------------------- the card shows the 📰 line

def test_card_shows_narrative_line_and_matches_verdict(conn):
    _seed(conn, extracted_at=int(time.time()), broadcast_dt="10-Jun-2026 14:01:00",
          narrative={**CUT_NARRATIVE, "dividend": 21.0})
    text, conf = format_result_quality(conn, symbol="INFY", direction="short")
    assert "SHORT bias" in text
    assert "guidance cut" in text.lower()
    assert "📰" in text and "Guidance cut" in text
    assert "dividend ₹21/share" in text
    assert "mgmt tone negative" in text
    assert conf > 0.6


def test_card_shows_sector_kpis_and_sources(conn):
    _seed(conn, extracted_at=int(time.time()), broadcast_dt="10-Jun-2026 14:01:00",
          narrative={"guidance": "cut", "cc_revenue_growth_pct": 2.1,
                     "tcv_usd_mn": 3100.0, "attrition_pct": 13.4,
                     "_sources": ["Press Release", "result PDF"]})
    text, _ = format_result_quality(conn, symbol="INFY", direction="short")
    assert "cc-rev +2.1%" in text
    assert "TCV $3,100 mn" in text
    assert "attrition 13.4%" in text
    assert "(press release, result pdf)" in text
