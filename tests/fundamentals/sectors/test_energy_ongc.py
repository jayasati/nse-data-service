"""ONGC Q4 FY26 case study — the energy real-PDF regression (playbook §2.2, P3).

The filing that motivated the per-sector engine. Headline PAT +3.1% (₹+202 cr)
looked like a beat, but it was propped two ways: other income rose ₹+553 cr and a
deferred-tax write-back held the bottom line up while **core operating profit
ex-other-income fell −11.9% YoY** (PBT − other income: ₹5,896 cr vs ₹6,693 cr).
The market gapped down ~1.5%. The old revenue-proxy engine called LONG
(revenue +2.7%); the engine must now call SHORT.

Numbers are the real standalone P&L from
fixtures/result_pdfs/ONGC_..._Financialresults31032026SD.pdf (page 9), so this
exercises the actual data layer (quarter_growth → core-operating derivation →
sector router) offline, the SBI/Axis/HDFC method.
"""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.fundamentals import from_results as fr
from nse_data.fundamentals.sectors import classify_result, sector_class_for
from nse_data.fundamentals.sectors.base import SectorClass
from nse_data.storage.db import apply_migrations

# Real ONGC standalone P&L, crore. (revenue, other_income, total_income,
# total_expenses, pbt, tax, pat, depreciation, finance_cost) per quarter end.
ONGC_ROWS = {
    "2025-03-31": (34982.23, 2074.69, 37056.92, 28289.49, 8767.43, 2319.15, 6448.28, 6078.53, 1190.09),   # Q4 FY25 (year-ago)
    "2025-12-31": (31546.51, 3093.74, 34640.25, 24038.19, 10602.06, 2230.21, 8371.85, 6609.88, 1153.63),  # Q3 FY26 (prev-q)
    "2026-03-31": (35928.18, 2627.73, 38555.91, 30032.12, 8523.79, 1873.82, 6649.97, 5624.72, 1144.97),   # Q4 FY26 (current)
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    for period, (rev, oi, ti, te, pbt, tax, pat, dep, fin) in ONGC_ROWS.items():
        c.execute(
            "INSERT INTO extracted_financials "
            "(symbol, period_ending, scope, revenue_cr, other_income_cr, total_income_cr, "
            " total_expenses_cr, pbt_cr, tax_cr, pat_cr, depreciation_cr, finance_cost_cr, extracted_at) "
            "VALUES ('ONGC', ?, 'standalone', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (period, rev, oi, ti, te, pbt, tax, pat, dep, fin),
        )
    c.commit()
    return c


def test_ongc_is_energy():
    assert sector_class_for("ONGC") == SectorClass.ENERGY


def test_core_operating_line_derived(conn):
    """The operating line the rule reads is core profit ex-other-income, derived
    from PBT and other income — it fell ~12% even though revenue rose."""
    g = fr.quarter_growth(conn, "ONGC", "2026-03-31", "standalone")
    assert g["yoy_revenue_pct"] == pytest.approx(2.7, abs=0.1)          # revenue UP
    assert g["yoy_pat_pct"] == pytest.approx(3.13, abs=0.05)            # headline UP
    assert g["yoy_other_income_pct"] == pytest.approx(26.66, abs=0.1)   # prop 1
    assert g["yoy_pbt_pct"] == pytest.approx(-2.78, abs=0.05)
    assert g["yoy_tax_pct"] == pytest.approx(-19.2, abs=0.1)            # prop 2 (write-back)
    assert g["yoy_operating_ex_oi_pct"] == pytest.approx(-11.9, abs=0.2)  # core-ex-OI proxy
    # true operating EBITDA (pbt + finance + depreciation − other income) also fell
    assert g["yoy_ebitda_pct"] == pytest.approx(-9.28, abs=0.2)


def test_operating_line_is_true_ebitda(conn):
    """With depreciation & finance costs extracted, the operating line is true
    EBITDA (not the core-ex-OI proxy); both agree ONGC's operating profit fell."""
    from nse_data.fundamentals.sectors.base import generic_operating_growth
    g = fr.quarter_growth(conn, "ONGC", "2026-03-31", "standalone")
    val, label = generic_operating_growth(g)
    assert "EBITDA" in label
    assert val == pytest.approx(-9.28, abs=0.2)


def test_ongc_verdict_is_short(conn):
    """The whole point: ONGC must now be a SHORT, not the old false LONG."""
    g = fr.quarter_growth(conn, "ONGC", "2026-03-31", "standalone")
    v = classify_result("ONGC", g)
    assert v.label == "low"
    assert v.direction == "short"
    assert "low_quality_beat" in v.flags          # headline up, operating down
    assert "other_income_propped" in v.flags      # ₹+553 cr other income
    assert "tax_propped" in v.flags               # deferred-tax write-back


def test_revenue_alone_would_not_short(conn):
    """Guard against regressing to the revenue proxy: on revenue (+2.7%) alone the
    operating line is a beat, so the SHORT must come from the core-ex-OI line."""
    g = fr.quarter_growth(conn, "ONGC", "2026-03-31", "standalone")
    # Strip every operating line → only revenue remains as the (weak) proxy.
    stripped = {k: v for k, v in g.items()
                if not any(t in k for t in ("operating_ex_oi", "ebitda", "ppop"))}
    from nse_data.fundamentals.sectors.base import generic_operating_growth
    val, label = generic_operating_growth(stripped)
    assert "revenue" in label and val is not None and val > 0   # revenue proxy would NOT short
