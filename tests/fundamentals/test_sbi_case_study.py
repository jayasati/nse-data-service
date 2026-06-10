"""SBI 8-May-2026 case study — end-to-end regression for Week 17.5 (S10).

Proves the catch-the-signal pipeline on the real SBI Q4 FY26 numbers, offline
(no LLM/PDF): sector-aware fields → persist + growth_json → quality verdict →
live result_quality signal → BFSI alert card → pre-print macro risk flag →
implied-vs-realized surprise.

The vision READ of the actual SBI PDF is the one piece that needs the filing
itself (8 May predates collection; no SBIN PDF is archived). The expected
extraction is pinned in ground_truth_bfsi/SBIN_Q4FY26.yaml for when the PDF is
supplied; everything downstream of extraction is exercised here.

SBI Q4 FY26 facts (from the forensic research): PAT ₹19,684 cr (+5.6% YoY) beat,
but PPOP ₹27,704 cr (−11.45% YoY, −15.7% QoQ), NII ₹44,380 cr, provisions
−36.6% YoY (propping PAT), treasury loss ₹1,471 cr on investments, GNPA 1.49% /
NNPA 0.39%. Macro into the print: Dec-2025 repo cut + 10Y G-sec hardening to ~7%.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import time

import pytest

from nse_data.bot.result_quality_message import format_result_quality
from nse_data.events.pre_screen import implied_vs_realized
from nse_data.fundamentals import from_results as fr
from nse_data.fundamentals.earnings_quality import classify_quality
from nse_data.market import macro_rates as mr
from nse_data.signals import compute, detect
from nse_data.signals.dedup import SignalDedup
from nse_data.storage.db import apply_migrations

# --- SBI Q4 FY26, standalone, crore (the numbers the market actually priced) ---
SBI_FIELDS = {
    "revenue_cr": 117996.0, "other_income_cr": 17314.0, "total_income_cr": 135310.0,
    "pat_cr": 19684.0, "net_interest_income_cr": 44380.0, "operating_profit_cr": 27704.0,
    "provisions_cr": 2700.0, "profit_on_sale_of_investments_cr": -1471.0,
    "gross_npa_pct": 1.49, "net_npa_pct": 0.39, "slippages_cr": 5521.0,
}
SBI_GROWTH = {
    "yoy_pat_pct": 5.58, "yoy_ppop_pct": -11.45, "qoq_ppop_pct": -15.7,
    "yoy_nii_pct": 6.5, "yoy_provisions_pct": -36.62, "yoy_other_income_pct": -28.94,
    "yoy_revenue_pct": 6.3,
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c, "migrations")
    yield c
    c.close()


def _seed_sbi_financials(conn, *, extracted_at: int, broadcast_dt: str):
    fr.persist_extraction(
        conn, symbol="SBIN", period_ending="2026-03-31", scope="standalone",
        fields=SBI_FIELDS, units_phrase="INR crore", confidence=0.9, strategy="vision",
        source_fingerprint="sbi-q4fy26", broadcast_dt=broadcast_dt, growth=SBI_GROWTH,
        now=extracted_at,
    )


# ---------------------------------------------------------------- S3: verdict

def test_quality_verdict_low_with_all_flags():
    v = classify_quality(SBI_GROWTH, SBI_FIELDS, is_bfsi=True)
    assert v.label == "low"
    assert v.direction == "short"
    assert set(v.flags) == {"low_quality_beat", "provision_propped", "treasury_hit"}


def test_clean_beat_is_high_long():
    clean = {"yoy_pat_pct": 18.0, "yoy_ppop_pct": 14.0, "yoy_provisions_pct": -2.0}
    v = classify_quality(clean, {"profit_on_sale_of_investments_cr": 300.0}, is_bfsi=True)
    assert v.label == "high" and v.direction == "long"


def test_operating_beat_with_provision_cut_is_not_short():
    """HDFC shape: PPOP & NII up, PAT up, but provisions down and a treasury loss.
    Operating line grew → must NOT flip to SHORT (provision/treasury are footnotes,
    not triggers). Regression for the false-positive the HDFC filing exposed."""
    g = {"yoy_pat_pct": 9.1, "yoy_ppop_pct": 4.6, "qoq_ppop_pct": 2.6,
         "yoy_nii_pct": 8.1, "yoy_provisions_pct": -18.3}
    v = classify_quality(g, {"profit_on_sale_of_investments_cr": -1076.0}, is_bfsi=True)
    assert v.direction != "short"
    assert v.label != "low"
    assert "low_quality_beat" not in v.flags


def test_provision_drop_alone_never_shorts_without_operating_miss():
    g = {"yoy_pat_pct": 6.0, "yoy_ppop_pct": 5.0, "yoy_provisions_pct": -40.0}
    v = classify_quality(g, {}, is_bfsi=True)
    assert v.direction != "short"


def test_outright_miss_shorts_when_pat_flat_and_operating_down():
    """Axis shape: PAT roughly flat/down, PPOP down, provisions surging. An
    outright operating miss must SHORT (result_miss) — the market gapped it down.
    Regression for the coverage gap the Axis filing + price action exposed."""
    g = {"yoy_pat_pct": -0.65, "yoy_ppop_pct": -6.87, "qoq_ppop_pct": -7.93,
         "yoy_provisions_pct": 159.1, "yoy_other_income_pct": -11.17}
    v = classify_quality(g, {}, is_bfsi=True)
    assert v.label == "low" and v.direction == "short"
    assert "result_miss" in v.flags
    assert "low_quality_beat" not in v.flags   # no beat — PAT was down


def test_hidden_miss_still_low_quality_beat_when_pat_up():
    g = {"yoy_pat_pct": 5.58, "yoy_ppop_pct": -11.45, "yoy_provisions_pct": -49.2}
    v = classify_quality(g, {"profit_on_sale_of_investments_cr": -1471.0}, is_bfsi=True)
    assert v.direction == "short" and "low_quality_beat" in v.flags
    assert "result_miss" not in v.flags


# ---------------------------------------------------------------- S2: persist

def test_persist_roundtrip_with_growth_json(conn):
    _seed_sbi_financials(conn, extracted_at=int(time.time()), broadcast_dt="08-May-2026 14:01:38")
    row = conn.execute(
        "SELECT operating_profit_cr, net_interest_income_cr, gross_npa_pct, "
        "profit_on_sale_of_investments_cr, growth_json FROM extracted_financials "
        "WHERE symbol='SBIN'"
    ).fetchone()
    assert row[0] == 27704.0 and row[1] == 44380.0 and row[2] == 1.49 and row[3] == -1471.0
    assert json.loads(row[4])["yoy_ppop_pct"] == -11.45


# ------------------------------------------------------- S4: live signal fire

def test_result_quality_signal_fires_short(conn, monkeypatch):
    now = detect.now_ist()
    filed = (now - datetime.timedelta(minutes=10)).strftime("%d-%b-%Y %H:%M:%S")
    _seed_sbi_financials(conn, extracted_at=int(now.timestamp()) - 120, broadcast_dt=filed)

    calls = []
    monkeypatch.setattr(detect, "_emit",
                        lambda *a, **k: calls.append((k["symbol"], k["signal_type"],
                                                      k["metrics"]["direction"])) or 1)
    monkeypatch.setattr(detect, "_load_listing_bars", lambda c: {"SBIN": 999})
    monkeypatch.setattr(compute, "compute_price_change", lambda c, s: (None, 1019.55))

    fired = detect._detect_result_quality(conn, None, SignalDedup(None), now.isoformat(), None, now)
    assert fired == 1
    assert ("SBIN", "result_quality_low", "short") in calls


def test_old_backfilled_result_does_not_fire(conn, monkeypatch):
    now = detect.now_ist()
    old = (now - datetime.timedelta(days=30)).strftime("%d-%b-%Y %H:%M:%S")  # filed long ago
    _seed_sbi_financials(conn, extracted_at=int(now.timestamp()) - 120, broadcast_dt=old)
    monkeypatch.setattr(detect, "_emit", lambda *a, **k: 1)
    monkeypatch.setattr(detect, "_load_listing_bars", lambda c: {"SBIN": 999})
    monkeypatch.setattr(compute, "compute_price_change", lambda c, s: (None, 1019.55))
    assert detect._detect_result_quality(conn, None, SignalDedup(None), now.isoformat(), None, now) == 0


# ---------------------------------------------------------------- S5: the card

def test_bfsi_card_shows_operating_lines(conn):
    _seed_sbi_financials(conn, extracted_at=int(time.time()), broadcast_dt="08-May-2026 14:01:38")
    text, conf = format_result_quality(conn, symbol="SBIN", direction="short")
    assert "SHORT bias" in text
    assert "PPOP ₹27,704 cr" in text
    assert "NII ₹44,380 cr" in text
    assert "Provisions" in text and "-36.6% YoY" in text
    assert "Treasury: loss ₹1,471 cr" in text
    assert "GNPA 1.49% / NNPA 0.39%" in text
    assert conf > 0.65   # clears the dispatch threshold


# ------------------------------------------------------- S6/S7: macro risk flag

def test_macro_state_and_bfsi_risk(conn):
    mr.record_rates(conn, "2025-09-30", repo_rate=6.50, gsec_10y_yield=6.55)
    mr.record_rates(conn, "2025-12-31", repo_rate=6.25, gsec_10y_yield=6.62)
    mr.record_rates(conn, "2026-01-31", repo_rate=6.00, gsec_10y_yield=6.80)
    mr.record_rates(conn, "2026-03-31", repo_rate=6.00, gsec_10y_yield=6.98)
    state = mr.macro_state(conn)
    assert state["rising_yields"] is True
    assert state["repo_cut_recent"] is True
    cls, note = mr.bfsi_earnings_risk(state)
    assert cls == mr.NIM_TREASURY_RISK
    assert "NIM" in note and "treasury" in note.lower()


# ---------------------------------------------------------------- S9: surprise

def test_implied_vs_realized_surprise(conn):
    conn.execute(
        "INSERT INTO earnings_setups (symbol, event_date, implied_move_pct, created_at) "
        "VALUES ('SBIN', '2026-05-08', 5.7, ?)", (int(time.time()),)
    )
    conn.commit()
    note = implied_vs_realized(conn, "SBIN", -7.0, 4.9)
    assert note is not None and "Larger-than-priced" in note
    assert implied_vs_realized(conn, "TCS", -7.0) is None  # no setup row
