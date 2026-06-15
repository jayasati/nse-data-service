"""HDFC AMC Q4 FY26 — the CAPMARKETS (fee-financial) real-filing regression.

Real standalone P&L from extracted_financials (XBRL, ₹ crore). An asset manager
is a FEE business, not a lender: management fees are the top line and finance
cost is immaterial, so the operating read is the plain revenue+PAT quality rule
(no bank NII/provision machinery, no lender add-back risk).

The teaching case: operating revenue grew +16.6% YoY while headline PAT FELL
-2.4% — purely because other income collapsed (123.8 → 11.2 cr). Law #1 (read
the operating line, not the headline) means this is still a LONG on operating
strength; the rule must NOT be dragged to a miss by a non-core PAT dip.
"""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.fundamentals import from_results as fr
from nse_data.fundamentals.sectors import SectorClass, classify_result, sector_class_for
from nse_data.storage.db import apply_migrations

# Real HDFC AMC standalone P&L, crore.
# (revenue, other_income, total_income, total_expenses, pbt, tax, pat).
HDFCAMC_ROWS = {
    "2025-03-31": (901.22, 123.78, 1025.00, 189.66, 835.34, 196.61, 638.73),   # Q4 FY25 (YoY base)
    "2025-12-31": (1074.25, 158.98, 1233.23, 218.63, 1014.60, 244.51, 770.09),  # Q3 FY26 (QoQ base)
    "2026-03-31": (1050.48, 11.19, 1061.67, 227.73, 833.94, 210.65, 623.29),    # Q4 FY26
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    for period, (rev, oi, ti, te, pbt, tax, pat) in HDFCAMC_ROWS.items():
        c.execute(
            "INSERT INTO extracted_financials "
            "(symbol, period_ending, scope, revenue_cr, other_income_cr, total_income_cr, "
            " total_expenses_cr, pbt_cr, tax_cr, pat_cr, extracted_at) "
            "VALUES ('HDFCAMC', ?, 'standalone', ?, ?, ?, ?, ?, ?, ?, 0)",
            (period, rev, oi, ti, te, pbt, tax, pat),
        )
    c.commit()
    return c


def test_capmarkets_symbols_route():
    assert sector_class_for("HDFCAMC") == SectorClass.CAPMARKETS
    assert sector_class_for("BSE") == SectorClass.CAPMARKETS


def test_fee_revenue_growth_reads_long_despite_pat_dip(conn):
    g = fr.quarter_growth(conn, "HDFCAMC", "2026-03-31", "standalone")
    assert g["yoy_revenue_pct"] == pytest.approx(16.6, abs=0.3)
    assert g["yoy_pat_pct"] < 0          # headline PAT actually fell
    v = classify_result("HDFCAMC", g, None)
    assert v.direction == "long"          # operating line (fees) grew → long
    assert "sector_capmarkets" in v.flags
