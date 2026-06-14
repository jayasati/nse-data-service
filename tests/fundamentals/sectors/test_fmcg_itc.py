"""ITC Q2 FY26 case study — the FMCG real-PDF regression (playbook §2.4, P5).

Numbers are the real standalone P&L from
fixtures/result_pdfs/fmcg/ITC__ITC_30102025162706_Results.pdf (page 4).
The print is the *inverse* of the FMCG inflation trap: revenue FELL −2.4% YoY
while EBITDA grew +3.5% — a revenue-proxy engine would have called this weak;
the EBITDA operating line reads it as the clean operating beat it was.
(Q2 FY26 PBT carries an ₹88 cr exceptional insurance settlement — immaterial
at this scale.) Volume growth / gross margin (the full FMCG tell) await P7.
"""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.fundamentals import from_results as fr
from nse_data.fundamentals.sectors import SectorClass, classify_result, sector_class_for
from nse_data.fundamentals.sectors.base import generic_operating_growth
from nse_data.storage.db import apply_migrations

# Real ITC standalone P&L, crore. (revenue, other_income, total_income,
# total_expenses, pbt, tax, pat, depreciation, finance_cost) per quarter end.
ITC_ROWS = {
    "2024-09-30": (19858.75, 873.70, 20732.45, 14115.66, 6616.79, 1640.94, 4975.85, 368.26, 11.94),  # Q2 FY25 (year-ago)
    "2025-06-30": (21058.98, 662.08, 21721.06, 15175.95, 6545.11, 1632.75, 4912.36, 365.31, 12.93),  # Q1 FY26 (prev-q)
    "2025-09-30": (19381.99, 897.97, 20279.96, 13516.57, 6851.47, 1671.65, 5179.82, 370.71, 15.88),  # Q2 FY26 (current)
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    for period, (rev, oi, ti, te, pbt, tax, pat, dep, fin) in ITC_ROWS.items():
        c.execute(
            "INSERT INTO extracted_financials "
            "(symbol, period_ending, scope, revenue_cr, other_income_cr, total_income_cr, "
            " total_expenses_cr, pbt_cr, tax_cr, pat_cr, depreciation_cr, finance_cost_cr, extracted_at) "
            "VALUES ('ITC', ?, 'standalone', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (period, rev, oi, ti, te, pbt, tax, pat, dep, fin),
        )
    c.commit()
    return c


def test_itc_routes_to_fmcg_built():
    assert sector_class_for("ITC") == SectorClass.FMCG


def test_operating_line_is_ebitda_not_revenue(conn):
    """Revenue fell −2.4% YoY but EBITDA grew +3.5% — the operating line must be
    EBITDA, or this clean quarter would have been misread as weak."""
    g = fr.quarter_growth(conn, "ITC", "2025-09-30", "standalone")
    assert g["yoy_revenue_pct"] == pytest.approx(-2.4, abs=0.1)
    assert g["yoy_ebitda_pct"] == pytest.approx(3.54, abs=0.2)
    val, label = generic_operating_growth(g)
    assert "EBITDA" in label
    assert val == pytest.approx(3.54, abs=0.2)


def test_itc_clean_beat_is_long(conn):
    """PAT +4.1% with EBITDA +3.5% and no non-core prop → clean LONG."""
    g = fr.quarter_growth(conn, "ITC", "2025-09-30", "standalone")
    v = classify_result("ITC", g)
    assert v.direction == "long"
    assert v.label == "high"
    assert "other_income_propped" not in v.flags
    assert "tax_propped" not in v.flags


def test_fmcg_price_led_revenue_with_margin_squeeze_shorts():
    """The §2.4 trap, synthetic: revenue up on price but EBITDA down (margin
    squeeze) and PAT held by other income → low-quality SHORT."""
    growth = {
        "yoy_revenue_pct": 9.0,        # price-led revenue growth
        "yoy_ebitda_pct": -4.0,        # margin squeeze — the real operating miss
        "yoy_pat_pct": 3.0,            # headline still up
        "yoy_other_income_pct": 25.0,  # propped
        "yoy_pbt_pct": -2.0,
        "yoy_tax_pct": -10.0,
    }
    v = classify_result("ITC", growth)
    assert v.direction == "short"
    assert v.label == "low"
    assert "low_quality_beat" in v.flags
    assert "other_income_propped" in v.flags


def test_fmcg_price_led_volume_caps_long():
    """Clean operating beat, but the narrative says volumes were flat while
    revenue rose — price-led, not demand-led → the long caps to neutral."""
    growth = {"yoy_revenue_pct": 8.0, "yoy_ebitda_pct": 6.0, "yoy_pat_pct": 7.0}
    v = classify_result("ITC", growth, narrative={"volume_growth": 0.0})
    assert v.direction is None and v.label == "neutral"
    assert "price_led_growth" in v.flags


def test_fmcg_volume_contraction_shorts_when_operating_flat():
    """UVG is the signal: an outright volume contraction with a flat operating
    line and no price offset (revenue also soft) is a demand red flag → SHORT."""
    growth = {"yoy_revenue_pct": -1.0, "yoy_ebitda_pct": 0.5, "yoy_pat_pct": 2.0}
    v = classify_result("ITC", growth, narrative={"volume_growth": -3.0})
    assert v.direction == "short" and v.label == "low"
    assert "volume_decline" in v.flags


def test_fmcg_commodity_tailwind_caps_long():
    """Margin beat driven by cheaper palm oil/packaging (material intensity fell
    and explains the margin gain) → not durable → capped to neutral."""
    growth = {
        "yoy_revenue_pct": 4.0, "yoy_ebitda_pct": 8.0, "yoy_pat_pct": 9.0,
        "yoy_ebitda_margin_chg_pp": 2.0, "yoy_material_ratio_chg_pp": -1.8,
    }
    v = classify_result("ITC", growth)
    assert v.direction is None and v.label == "neutral"
    assert "commodity_tailwind" in v.flags
