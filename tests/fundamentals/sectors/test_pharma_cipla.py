"""Cipla Q2 FY26 case study — the pharma real-PDF regression (§2.6, P5).

Numbers are the real consolidated P&L from
fixtures/result_pdfs/pharma/CIPLA__CIPLA_30102025124916_...30092025signed.pdf
(page 3). The shape: operating EBITDA flat (+0.5% YoY) while other income
jumped +41% and PAT grew +3.7% — a healthy-looking headline on a flat core.
The engine must stay conservatively NEUTRAL: no short (no confirmed operating
decline, playbook §0) and no false other-income prop (the prop requires the
core to have actually fallen). US sales / USFDA status (the full pharma tell —
a warning letter is binary and huge) await P7 text ingestion.
"""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.fundamentals import from_results as fr
from nse_data.fundamentals.sectors import SectorClass, classify_result, sector_class_for
from nse_data.fundamentals.sectors.base import generic_operating_growth
from nse_data.storage.db import apply_migrations

# Real Cipla consolidated P&L, crore. (revenue, other_income, total_income,
# total_expenses, pbt, tax, pat, depreciation, finance_cost) per quarter end.
CIPLA_ROWS = {
    "2024-09-30": (7051.02, 190.61, 7241.63, 5452.57, 1789.06, 483.04, 1305.01, 271.74, 15.40),  # Q2 FY25 (year-ago)
    "2025-06-30": (6957.47, 258.56, 7216.03, 5446.10, 1769.93, 477.88, 1291.61, 252.72, 14.05),  # Q1 FY26 (prev-q)
    "2025-09-30": (7589.44, 268.95, 7858.39, 6004.86, 1853.53, 500.46, 1353.37, 296.99, 13.18),  # Q2 FY26 (current)
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    for period, (rev, oi, ti, te, pbt, tax, pat, dep, fin) in CIPLA_ROWS.items():
        c.execute(
            "INSERT INTO extracted_financials "
            "(symbol, period_ending, scope, revenue_cr, other_income_cr, total_income_cr, "
            " total_expenses_cr, pbt_cr, tax_cr, pat_cr, depreciation_cr, finance_cost_cr, extracted_at) "
            "VALUES ('CIPLA', ?, 'consolidated', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (period, rev, oi, ti, te, pbt, tax, pat, dep, fin),
        )
    c.commit()
    return c


def test_cipla_routes_to_pharma_built():
    assert sector_class_for("CIPLA") == SectorClass.PHARMA


def test_flat_operating_line_read_correctly(conn):
    """EBITDA roughly flat (+0.5% YoY) despite revenue +7.6% and OI +41%."""
    g = fr.quarter_growth(conn, "CIPLA", "2025-09-30", "consolidated")
    assert g["yoy_revenue_pct"] == pytest.approx(7.6, abs=0.1)
    assert g["yoy_other_income_pct"] == pytest.approx(41.1, abs=0.3)
    val, label = generic_operating_growth(g)
    assert "EBITDA" in label
    assert val == pytest.approx(0.5, abs=0.2)


def test_cipla_flat_print_stays_neutral_no_false_prop(conn):
    """Flat core + big OI jump must NOT short and must NOT flag the OI prop
    (the prop requires the operating line to have fallen) — the pharma
    no-false-positive guarantee."""
    g = fr.quarter_growth(conn, "CIPLA", "2025-09-30", "consolidated")
    v = classify_result("CIPLA", g)
    assert v.direction is None
    assert v.label == "neutral"
    assert "other_income_propped" not in v.flags
    assert "result_miss" not in v.flags


def test_pharma_one_off_propped_quarter_shorts():
    """The §2.6 trap, synthetic: core EBITDA down (US price erosion) while a
    one-off (para-IV / settlement, lands in other income) holds the headline →
    low-quality SHORT."""
    growth = {
        "yoy_pat_pct": 8.0,
        "yoy_ebitda_pct": -7.0,        # US erosion — the operating miss
        "yoy_revenue_pct": 1.0,
        "yoy_other_income_pct": 60.0,  # the one-off prop
        "yoy_pbt_pct": -5.0,
        "yoy_tax_pct": -20.0,
    }
    v = classify_result("CIPLA", growth)
    assert v.direction == "short"
    assert v.label == "low"
    assert "low_quality_beat" in v.flags
    assert "other_income_propped" in v.flags
