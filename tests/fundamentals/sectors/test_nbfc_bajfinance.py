"""Bajaj Finance Q4 FY26 — the NBFC real-filing regression.

Real standalone P&L from extracted_financials (XBRL, ₹ crore). A non-bank lender
files interest income as its top line and carries finance cost as an OPERATING
expense, so the generic EBITDA add-back would fabricate the operating line —
hence NBFCs route to their own rule, not GENERIC. Here interest/operating income
grew +16.7% YoY and PAT +22.8% → a clean LONG, read off the income line directly
(no add-back). NII / provisions / GNPA are not yet extracted, so the verdict
leans on the income-vs-PAT divergence.
"""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.fundamentals import from_results as fr
from nse_data.fundamentals.sectors import SectorClass, classify_result, sector_class_for
from nse_data.storage.db import apply_migrations

# Real Bajaj Finance standalone P&L, crore.
# (revenue/interest income, other_income, total_income, total_expenses, pbt, tax, pat).
BAJFIN_ROWS = {
    "2025-03-31": (15796.96, 11.44, 15808.40, 10903.53, 4904.87, 964.43, 3940.44),  # Q4 FY25 (YoY base)
    "2025-12-31": (18067.89, 0.40, 18068.29, 13296.66, 5938.01, 1357.49, 4580.52),  # Q3 FY26 (QoQ base)
    "2026-03-31": (18430.12, 0.52, 18430.64, 11946.30, 6484.34, 1644.84, 4839.50),  # Q4 FY26
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    for period, (rev, oi, ti, te, pbt, tax, pat) in BAJFIN_ROWS.items():
        c.execute(
            "INSERT INTO extracted_financials "
            "(symbol, period_ending, scope, revenue_cr, other_income_cr, total_income_cr, "
            " total_expenses_cr, pbt_cr, tax_cr, pat_cr, extracted_at) "
            "VALUES ('BAJFINANCE', ?, 'standalone', ?, ?, ?, ?, ?, ?, ?, 0)",
            (period, rev, oi, ti, te, pbt, tax, pat),
        )
    c.commit()
    return c


def test_nbfc_symbols_route():
    assert sector_class_for("BAJFINANCE") == SectorClass.NBFC
    assert sector_class_for("CHOLAFIN") == SectorClass.NBFC


def test_bajaj_finance_clean_beat_reads_long(conn):
    g = fr.quarter_growth(conn, "BAJFINANCE", "2026-03-31", "standalone")
    assert g["yoy_revenue_pct"] == pytest.approx(16.7, abs=0.3)
    assert g["yoy_pat_pct"] == pytest.approx(22.8, abs=0.3)
    v = classify_result("BAJFINANCE", g, None)
    assert v.direction == "long"
    assert "sector_nbfc" in v.flags
    # lender read: NO generic EBITDA add-back fabricated the operating line
    assert "sector_generic" not in v.flags
