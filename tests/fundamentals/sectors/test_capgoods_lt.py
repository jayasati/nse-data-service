"""L&T Q2 FY26 case study — the capital-goods real-PDF regression (§2.8, P5).

Numbers are the real consolidated P&L from
fixtures/result_pdfs/capgoods/LT__PAM_29102025171032_ResultsSep2025.pdf
(page 9). A clean execution quarter: EBITDA +7.0% YoY, PAT +14.1% → LONG.
(Context from the same filing, awaiting P7 for systematic capture: Q2 order
inflow ₹115,784 cr +45% YoY; order book ₹667,047 cr — book-to-bill ~2.5x,
corroborating the long.)

Routing matters here: there is no constituent-backed capital-goods index, so
LT resolves via the ``SYMBOL_TO_CLASS`` override — and ABB/SIEMENS/BHEL, which
the index data files under NIFTY ENERGY, must override to CAPGOODS too.
"""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.fundamentals import from_results as fr
from nse_data.fundamentals.sectors import SectorClass, classify_result, sector_class_for
from nse_data.fundamentals.sectors.base import generic_operating_growth
from nse_data.storage.db import apply_migrations

# Real L&T consolidated P&L, crore. (revenue, other_income, total_income,
# total_expenses, pbt, tax, pat, depreciation, finance_cost) per quarter end.
# PAT is after share of JV/associates, as printed (row 9 of the statement).
LT_ROWS = {
    "2024-09-30": (61554.58, 1101.27, 62655.85, 57100.76, 5555.09, 1442.28, 4098.84, 1023.84, 884.38),  # Q2 FY25 (year-ago)
    "2025-06-30": (63678.92, 1356.78, 65035.70, 59176.17, 5859.53, 1533.96, 4318.17, 1033.30, 781.61),  # Q1 FY26 (prev-q)
    "2025-09-30": (67983.53, 1384.28, 69367.81, 63031.70, 6336.11, 1649.02, 4678.01, 1091.77, 762.81),  # Q2 FY26 (current)
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    for period, (rev, oi, ti, te, pbt, tax, pat, dep, fin) in LT_ROWS.items():
        c.execute(
            "INSERT INTO extracted_financials "
            "(symbol, period_ending, scope, revenue_cr, other_income_cr, total_income_cr, "
            " total_expenses_cr, pbt_cr, tax_cr, pat_cr, depreciation_cr, finance_cost_cr, extracted_at) "
            "VALUES ('LT', ?, 'consolidated', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (period, rev, oi, ti, te, pbt, tax, pat, dep, fin),
        )
    c.commit()
    return c


def test_capgoods_symbol_overrides_route_correctly():
    """LT has no index mapping; ABB/SIEMENS/BHEL sit in NIFTY ENERGY in the
    constituent data — all must resolve to CAPGOODS via SYMBOL_TO_CLASS."""
    for sym in ("LT", "BEL", "ABB", "SIEMENS", "BHEL", "CUMMINSIND"):
        assert sector_class_for(sym) == SectorClass.CAPGOODS, sym


def test_lt_clean_execution_quarter_is_long(conn):
    g = fr.quarter_growth(conn, "LT", "2025-09-30", "consolidated")
    val, label = generic_operating_growth(g)
    assert "EBITDA" in label
    assert val == pytest.approx(7.0, abs=0.2)
    assert g["yoy_pat_pct"] == pytest.approx(14.1, abs=0.1)
    v = classify_result("LT", g)
    assert v.direction == "long"
    assert v.label == "high"
    # other income +25.7% must NOT flag: the operating line grew (playbook §0).
    assert "other_income_propped" not in v.flags


def test_capgoods_drawdown_quarter_shorts():
    """The §2.8 trap, synthetic: execution revenue up (order-book drawdown) but
    EBITDA down on margin/working-capital stress, PAT held by other income →
    low-quality SHORT. (Order-inflow corroboration itself awaits P7.)"""
    growth = {
        "yoy_pat_pct": 1.0,
        "yoy_ebitda_pct": -5.0,        # margin stress — the operating miss
        "yoy_revenue_pct": 8.0,        # drawdown-led execution
        "yoy_other_income_pct": 28.0,
        "yoy_pbt_pct": -3.0,
        "yoy_tax_pct": -9.0,
    }
    v = classify_result("LT", growth)
    assert v.direction == "short"
    assert v.label == "low"
    assert "low_quality_beat" in v.flags
    assert "other_income_propped" in v.flags
