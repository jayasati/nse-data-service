"""Maruti Suzuki Q2 FY26 case study — the auto real-PDF regression (§2.5, P5).

Numbers are the real standalone P&L from
fixtures/result_pdfs/auto/MARUTI__MARUTIASHISH_31102025141548_...30Sep2025.pdf
(page 2, reported in INR million, ÷10 to crore).

The instructive shape: headline PAT +7.3% YoY, but PBT fell −16.7% and the tax
charge fell −52.8% (the year-ago quarter carried a one-off deferred-tax hit),
while operating EBITDA was flat (+0.4%). The engine must read this as a
conservative NEUTRAL with the ``tax_propped`` caveat — neither a clean long
(the headline is tax-flattered) nor a short (no confirmed operating decline,
playbook §0). Volumes / realization-per-unit (the full auto tell) await the
monthly-volume feed and P7.
"""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.fundamentals import from_results as fr
from nse_data.fundamentals.sectors import SectorClass, classify_result, sector_class_for
from nse_data.fundamentals.sectors.base import generic_operating_growth
from nse_data.storage.db import apply_migrations

# Real Maruti standalone P&L, crore. (revenue, other_income, total_income,
# total_expenses, pbt, tax, pat, depreciation, finance_cost) per quarter end.
MARUTI_ROWS = {
    "2024-09-30": (37202.8, 1475.0, 38677.8, 33577.3, 5100.5, 2031.3, 3069.2, 750.9, 40.2),   # Q2 FY25 (year-ago)
    "2025-06-30": (38413.6, 1823.0, 40236.6, 35402.4, 4834.2, 1122.5, 3711.7, 937.5, 46.6),   # Q1 FY26 (prev-q)
    "2025-09-30": (42100.8, 913.1, 43013.9, 38762.9, 4251.0, 957.9, 3293.1, 1039.2, 57.0),    # Q2 FY26 (current)
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    for period, (rev, oi, ti, te, pbt, tax, pat, dep, fin) in MARUTI_ROWS.items():
        c.execute(
            "INSERT INTO extracted_financials "
            "(symbol, period_ending, scope, revenue_cr, other_income_cr, total_income_cr, "
            " total_expenses_cr, pbt_cr, tax_cr, pat_cr, depreciation_cr, finance_cost_cr, extracted_at) "
            "VALUES ('MARUTI', ?, 'standalone', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (period, rev, oi, ti, te, pbt, tax, pat, dep, fin),
        )
    c.commit()
    return c


def test_maruti_routes_to_auto_built():
    assert sector_class_for("MARUTI") == SectorClass.AUTO


def test_headline_is_tax_flattered(conn):
    """PAT +7.3% looks healthy; PBT −16.7% with tax −52.8% says the growth is a
    tax-comparison artefact, and EBITDA (+0.4%) says operations were flat."""
    g = fr.quarter_growth(conn, "MARUTI", "2025-09-30", "standalone")
    assert g["yoy_pat_pct"] == pytest.approx(7.3, abs=0.1)
    assert g["yoy_pbt_pct"] == pytest.approx(-16.7, abs=0.1)
    assert g["yoy_tax_pct"] == pytest.approx(-52.8, abs=0.2)
    val, label = generic_operating_growth(g)
    assert "EBITDA" in label
    assert val == pytest.approx(0.4, abs=0.2)


def test_maruti_neutral_with_tax_prop_caveat(conn):
    """Flat operating line → no short (playbook §0); tax-flattered headline →
    no clean long either. NEUTRAL with the tax_propped caveat."""
    g = fr.quarter_growth(conn, "MARUTI", "2025-09-30", "standalone")
    v = classify_result("MARUTI", g)
    assert v.direction is None
    assert v.label == "neutral"
    assert "tax_propped" in v.flags
    assert "other_income_propped" not in v.flags   # other income actually FELL −38%


def test_auto_commodity_squeeze_low_quality_beat_shorts():
    """The §2.5 trap, synthetic: EBITDA down on input costs while PAT is held
    up by other income → low-quality SHORT, props corroborate."""
    growth = {
        "yoy_pat_pct": 4.0,
        "yoy_ebitda_pct": -6.0,        # input-cost squeeze — the operating miss
        "yoy_revenue_pct": 5.0,
        "yoy_other_income_pct": 30.0,
        "yoy_pbt_pct": -4.0,
        "yoy_tax_pct": -15.0,
        "yoy_material_ratio_chg_pp": 2.5,   # materials got dearer as % of revenue
    }
    v = classify_result("MARUTI", growth)
    assert v.direction == "short"
    assert v.label == "low"
    assert "low_quality_beat" in v.flags
    assert "other_income_propped" in v.flags
    assert "commodity_headwind" in v.flags          # the input squeeze, contextualised


def test_auto_commodity_tailwind_caps_long_to_neutral():
    """The headline edge: an EBITDA beat that rode cheaper steel/aluminium (the
    material-cost ratio fell, and that drop explains most of the margin gain) is
    NOT a durable demand-led beat — the long is capped to a flagged neutral."""
    growth = {
        "yoy_pat_pct": 12.0,
        "yoy_ebitda_pct": 9.0,                 # operating "beat"
        "yoy_revenue_pct": 3.0,
        "yoy_ebitda_margin_chg_pp": 2.5,       # margin expanded 2.5pp
        "yoy_material_ratio_chg_pp": -2.2,     # ...and inputs fell 2.2pp → explains it
    }
    v = classify_result("MARUTI", growth)
    assert v.direction is None and v.label == "neutral"
    assert "commodity_tailwind" in v.flags


def test_auto_volume_led_beat_stays_long():
    """A genuine demand-led beat: margin expanded far more than the small input
    tailwind, so volume/mix drove it — stays LONG, not capped."""
    growth = {
        "yoy_pat_pct": 20.0,
        "yoy_ebitda_pct": 22.0,
        "yoy_revenue_pct": 15.0,
        "yoy_ebitda_margin_chg_pp": 4.0,       # big margin expansion
        "yoy_material_ratio_chg_pp": -0.8,     # only a small input help (<60% of gain)
    }
    v = classify_result("MARUTI", growth)
    assert v.direction == "long" and v.label == "high"
    assert "commodity_tailwind" not in v.flags
