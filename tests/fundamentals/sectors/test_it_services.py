"""IT Services sector rule (playbook §2.3, P4).

IT runs the shared operating-quality verdict (base.classify_operating_quality):
the operating line (EBITDA/EBIT) vs the headline, plus the other-income / tax
props — the same validated logic BFSI and energy use.

Two REAL filings (numbers read from the PDFs in
fixtures/result_pdfs/it/) harden the no-false-positive behaviour — a healthy IT
quarter with a big other-income jump must NOT short:
  * INFY Q2 FY26 (standalone, YoY) — EBITDA +5.3% operating beat, other income
    +30.6%, PAT +13.9% → clean LONG; the prop guard stays silent because the
    operating line grew.
  * TCS Q1 FY26 (consolidated, QoQ — the interim statement carries no year-ago
    column) — EBITDA −0.6% flat, other income +61% → conservative NEUTRAL, no short.
The synthetic cases below exercise the SHORT branch (no real low-quality IT
filing is in the set yet). IT-specific signals (guidance, TCV, cc-revenue) are
pending P7.
"""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.fundamentals import from_results as fr
from nse_data.fundamentals.sectors import SectorClass, classify_result, sector_class_for
from nse_data.fundamentals.sectors.base import generic_operating_growth
from nse_data.storage.db import apply_migrations


def _ins(c, sym, period, rev, oi, ti, te, pbt, tax, pat, dep, fin):
    c.execute(
        "INSERT INTO extracted_financials "
        "(symbol, period_ending, scope, revenue_cr, other_income_cr, total_income_cr, "
        " total_expenses_cr, pbt_cr, tax_cr, pat_cr, depreciation_cr, finance_cost_cr, extracted_at) "
        "VALUES (?, ?, 'standalone', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (sym, period, rev, oi, ti, te, pbt, tax, pat, dep, fin),
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    # INFY Q2 FY26 standalone — (Sep'25 current, Sep'24 year-ago) → YoY.
    _ins(c, "INFY", "2024-09-30", 34257, 1737, 35994, 26587, 9407, 2594, 6813, 670, 61)
    _ins(c, "INFY", "2025-09-30", 36907, 2268, 39175, 28706, 10469, 2710, 7759, 595, 52)
    # TCS Q1 FY26 consolidated — (Jun'25 current, Mar'25 prev-q) → QoQ only.
    _ins(c, "TCS", "2025-03-31", 64479, 1028, 65507, 49105, 16402, 4109, 12293, 1379, 227)
    _ins(c, "TCS", "2025-06-30", 63437, 1660, 65097, 48118, 16979, 4160, 12819, 1361, 195)
    c.commit()
    return c


def test_infy_routes_to_it_built():
    assert sector_class_for("INFY") == SectorClass.IT
    assert sector_class_for("TCS") == SectorClass.IT


def test_infy_real_clean_beat_is_long(conn):
    """Real INFY Q2 FY26: operating EBITDA grew (+5.3% YoY) → clean LONG. Other
    income +30.6% does NOT trigger a short because the operating line is up — the
    no-false-positive guarantee on a healthy IT print."""
    g = fr.quarter_growth(conn, "INFY", "2025-09-30", "standalone")
    val, label = generic_operating_growth(g)
    assert "EBITDA" in label and val == pytest.approx(5.3, abs=0.3)
    v = classify_result("INFY", g)
    assert v.direction == "long"
    assert v.label == "high"
    assert "other_income_propped" not in v.flags


def test_tcs_real_qoq_flat_not_short(conn):
    """Real TCS Q1 FY26 (QoQ-only interim): operating EBITDA roughly flat
    (−0.6%) → NEUTRAL, never a short. A confirmed operating DECLINE is required;
    a big QoQ other-income jump (+61%) alone never fires (playbook §0)."""
    g = fr.quarter_growth(conn, "TCS", "2025-06-30", "standalone")
    val, label = generic_operating_growth(g)
    assert "EBITDA" in label and val is not None and -2.0 < val < 2.0   # flat, materiality band
    v = classify_result("TCS", g)
    assert v.direction is None
    assert v.label == "neutral"


def test_it_low_quality_beat_shorts():
    """The IT trap: soft operating quarter papered over by treasury other income
    and a lower tax charge. Operating EBITDA down → SHORT, props corroborate."""
    growth = {
        "yoy_pat_pct": 6.0,          # headline beat
        "yoy_ebitda_pct": -5.0,      # operating line DOWN (the miss)
        "yoy_revenue_pct": 3.0,      # revenue up — would have hidden the miss
        "yoy_other_income_pct": 35.0,  # treasury other income propping PAT
        "yoy_pbt_pct": -3.0,
        "yoy_tax_pct": -12.0,        # lower tax charge holding the headline
    }
    v = classify_result("INFY", growth)
    assert v.direction == "short"
    assert v.label == "low"
    assert "low_quality_beat" in v.flags
    assert "other_income_propped" in v.flags
    assert "tax_propped" in v.flags


def test_it_clean_beat_is_long():
    """Operating line genuinely growing, no props → clean beat, long."""
    growth = {
        "yoy_pat_pct": 12.0,
        "yoy_ebitda_pct": 11.0,      # operating line UP
        "yoy_revenue_pct": 10.0,
        "yoy_other_income_pct": 4.0,
    }
    v = classify_result("INFY", growth)
    assert v.direction == "long"
    assert v.label == "high"


def test_it_high_other_income_but_operating_grows_not_short():
    """Big other income does NOT trigger a short when the operating line is up —
    the prop only caps the upside (caveat), never fires alone (playbook §0)."""
    growth = {
        "yoy_pat_pct": 15.0,
        "yoy_ebitda_pct": 8.0,       # operating UP → not a miss
        "yoy_other_income_pct": 40.0,
    }
    v = classify_result("INFY", growth)
    assert v.direction != "short"
    assert v.label in ("high", "neutral")
