"""Oberoi Realty Q2 FY26 case study — the realty real-PDF regression.

Numbers are the real consolidated P&L from fixtures/result_pdfs/realty/
OBEROIRLTY__..._Financial_Results_30092025.pdf (page 5, ₹ lakh → crore;
scanned filing, transcribed from the rendered page). A completion-heavy
quarter: EBITDA +26.4% YoY, PAT +29.0% → clean LONG — and other income +69.9%
must NOT flag, because the operating line genuinely grew.

The same numbers also showcase realty's lumpiness (the §2.9 caveat): revenue
swung +80% QoQ on handover timing alone — which is why the market prices
pre-sales/bookings (P7 narrative), and why the P&L verdict stays deliberately
conservative for this sector.
"""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.fundamentals import from_results as fr
from nse_data.fundamentals.sectors import SectorClass, classify_result, sector_class_for
from nse_data.fundamentals.sectors.base import generic_operating_growth
from nse_data.storage.db import apply_migrations

# Real Oberoi Realty consolidated P&L, crore. (revenue, other_income,
# total_income, total_expenses, pbt, tax, pat, depreciation, finance_cost).
# PBT is as printed (after share of JV profit); no exceptional items.
OBEROI_ROWS = {
    "2024-09-30": (1319.89, 38.73, 1358.62, 578.64, 782.47, 193.03, 589.44, 20.83, 51.70),  # Q2 FY25
    "2025-06-30": (987.55, 86.43, 1073.98, 573.78, 506.96, 85.71, 421.25, 31.62, 74.95),    # Q1 FY26
    "2025-09-30": (1779.04, 65.80, 1844.84, 863.35, 993.14, 232.88, 760.26, 33.43, 71.17),  # Q2 FY26
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    for period, (rev, oi, ti, te, pbt, tax, pat, dep, fin) in OBEROI_ROWS.items():
        c.execute(
            "INSERT INTO extracted_financials "
            "(symbol, period_ending, scope, revenue_cr, other_income_cr, total_income_cr, "
            " total_expenses_cr, pbt_cr, tax_cr, pat_cr, depreciation_cr, finance_cost_cr, extracted_at) "
            "VALUES ('OBEROIRLTY', ?, 'consolidated', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (period, rev, oi, ti, te, pbt, tax, pat, dep, fin),
        )
    c.commit()
    return c


def test_realty_symbols_route():
    assert sector_class_for("OBEROIRLTY") == SectorClass.REALTY
    assert sector_class_for("DLF") == SectorClass.REALTY


def test_completion_quarter_reads_as_clean_beat(conn):
    g = fr.quarter_growth(conn, "OBEROIRLTY", "2025-09-30", "consolidated")
    val, label = generic_operating_growth(g)
    assert "EBITDA" in label
    assert val == pytest.approx(26.4, abs=0.3)
    assert g["yoy_pat_pct"] == pytest.approx(29.0, abs=0.2)
    v = classify_result("OBEROIRLTY", g)
    assert v.direction == "long"
    assert v.label == "high"
    # other income +69.9% must NOT flag — the operating line genuinely grew
    assert "other_income_propped" not in v.flags
    assert "sector_generic" not in v.flags     # realty has its own spec, not GENERIC


def test_lumpy_qoq_visible_in_growth(conn):
    """The §2.9 caveat in numbers: +80% revenue QoQ on handover timing."""
    g = fr.quarter_growth(conn, "OBEROIRLTY", "2025-09-30", "consolidated")
    assert g["qoq_revenue_pct"] == pytest.approx(80.1, abs=0.5)


def test_realty_oi_propped_timing_quarter_shorts():
    """The §2.9 trap, synthetic: a handover-light quarter (EBITDA down) with the
    headline held up by other income / a tax credit → low-quality SHORT."""
    growth = {
        "yoy_pat_pct": 3.0,
        "yoy_ebitda_pct": -9.0,        # completion-light — the operating miss
        "yoy_revenue_pct": -4.0,
        "yoy_other_income_pct": 55.0,  # treasury income propping PAT
        "yoy_pbt_pct": -6.0,
        "yoy_tax_pct": -20.0,
    }
    v = classify_result("OBEROIRLTY", growth)
    assert v.direction == "short"
    assert v.label == "low"
    assert "low_quality_beat" in v.flags
    assert "other_income_propped" in v.flags