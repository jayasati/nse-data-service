"""JSW Steel Q2 FY26 case study — the metals real-PDF regression (§2.7, P5).

Numbers are the real consolidated P&L from
fixtures/result_pdfs/metals/JSWSTEEL__Supriya_17102025145203_Results.pdf
(page 21). A genuine cyclical upswing: EBITDA +39.6% YoY on realization/volume
strength, PAT up ~4x (the year-ago PBT also carried a ₹342 cr exceptional
charge) → clean LONG. EBITDA/tonne & volumes (the full metals tell) await P7.
"""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.fundamentals import from_results as fr
from nse_data.fundamentals.sectors import SectorClass, classify_result, sector_class_for
from nse_data.fundamentals.sectors.base import generic_operating_growth
from nse_data.storage.db import apply_migrations

# Real JSW Steel consolidated P&L, crore. (revenue, other_income, total_income,
# total_expenses, pbt, tax, pat, depreciation, finance_cost) per quarter end.
# PBT is as printed: after share of JV/associates and (FY25) exceptional items.
JSW_ROWS = {
    "2024-09-30": (39684, 153, 39837, 38644, 789, 385, 404, 2267, 2130),     # Q2 FY25 (year-ago)
    "2025-06-30": (43147, 350, 43497, 40325, 3072, 863, 2209, 2537, 2217),   # Q1 FY26 (prev-q)
    "2025-09-30": (45152, 284, 45436, 43004, 2344, 698, 1646, 2554, 2413),   # Q2 FY26 (current)
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    for period, (rev, oi, ti, te, pbt, tax, pat, dep, fin) in JSW_ROWS.items():
        c.execute(
            "INSERT INTO extracted_financials "
            "(symbol, period_ending, scope, revenue_cr, other_income_cr, total_income_cr, "
            " total_expenses_cr, pbt_cr, tax_cr, pat_cr, depreciation_cr, finance_cost_cr, extracted_at) "
            "VALUES ('JSWSTEEL', ?, 'consolidated', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (period, rev, oi, ti, te, pbt, tax, pat, dep, fin),
        )
    c.commit()
    return c


def test_jswsteel_routes_to_metals_built():
    assert sector_class_for("JSWSTEEL") == SectorClass.METALS


def test_cyclical_upswing_reads_as_ebitda_beat(conn):
    g = fr.quarter_growth(conn, "JSWSTEEL", "2025-09-30", "consolidated")
    assert g["yoy_revenue_pct"] == pytest.approx(13.8, abs=0.1)
    val, label = generic_operating_growth(g)
    assert "EBITDA" in label
    assert val == pytest.approx(39.6, abs=0.5)


def test_jswsteel_clean_beat_is_long(conn):
    g = fr.quarter_growth(conn, "JSWSTEEL", "2025-09-30", "consolidated")
    v = classify_result("JSWSTEEL", g)
    assert v.direction == "long"
    assert v.label == "high"
    assert not [f for f in v.flags if f.endswith("_propped")]


def test_metals_downcycle_propped_headline_shorts():
    """The §2.7 trap, synthetic: realization down-cycle (EBITDA falls) while
    PAT is held by forex/other income → low-quality SHORT."""
    growth = {
        "yoy_pat_pct": 2.0,
        "yoy_ebitda_pct": -12.0,       # realization down — the operating miss
        "yoy_revenue_pct": -1.0,
        "yoy_other_income_pct": 45.0,  # forex / treasury prop
        "yoy_pbt_pct": -8.0,
        "yoy_tax_pct": -25.0,
    }
    v = classify_result("JSWSTEEL", growth)
    assert v.direction == "short"
    assert v.label == "low"
    assert "low_quality_beat" in v.flags
    assert "other_income_propped" in v.flags
    assert "tax_propped" in v.flags
