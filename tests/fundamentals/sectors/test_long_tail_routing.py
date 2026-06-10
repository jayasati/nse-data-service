"""Long-tail sector routing + the GENERIC rule (the "every stock gets read"
upgrade).

Three guarantees:
  1. NSE's quote-metadata taxonomy routes the long tail (cement → generic,
     telecom → generic, a non-index bank → bfsi, hospitals → pharma, …).
  2. **The lender guard**: non-bank financials (NBFC/broker/AMC/insurer/
     holding) are NEVER routed — for a lender, finance cost is operating, so
     the generic EBITDA derivation would fabricate the operating line. They
     stay out-of-scope neutral.
  3. GENERIC verdicts run the shared operating-quality rule and carry the
     ``sector_generic`` honesty flag on every non-neutral verdict.
"""
from __future__ import annotations

import pytest

from nse_data.fundamentals.sectors import SectorClass, classify_result, sector_class_for
from nse_data.fundamentals.sectors.base import class_for_metadata


# --- class_for_metadata: the taxonomy mapping -----------------------------------

@pytest.mark.parametrize("sector,industry,expected", [
    ("Financial Services", "Private Sector Bank", SectorClass.BFSI),
    ("Financial Services", "Public Sector Bank", SectorClass.BFSI),
    ("Financial Services", "Other Bank", SectorClass.BFSI),
    # the lender guard — every non-bank financial stays unrouted:
    ("Financial Services", "Non Banking Financial Company (NBFC)", None),
    ("Financial Services", "Stockbroking & Allied", None),
    ("Financial Services", "Investment Company", None),
    ("Financial Services", "Holding Company", None),
    ("Healthcare", "Pharmaceuticals", SectorClass.PHARMA),
    ("Healthcare", "Hospital", SectorClass.PHARMA),
    ("Information Technology", "Software Products", SectorClass.IT),
    ("Fast Moving Consumer Goods", "Packaged Foods", SectorClass.FMCG),
    ("Energy", "Refineries & Marketing", SectorClass.ENERGY),
    ("Utilities", "Power Generation", SectorClass.ENERGY),
    ("Commodities", "Aluminium", SectorClass.METALS),
    ("Commodities", "Iron & Steel", SectorClass.METALS),
    ("Commodities", "Cement & Cement Products", SectorClass.GENERIC),
    ("Commodities", "Specialty Chemicals", SectorClass.GENERIC),
    ("Industrials", "Heavy Electrical Equipment", SectorClass.CAPGOODS),
    ("Industrials", "Aerospace & Defense", SectorClass.CAPGOODS),
    ("Industrials", "Commercial Vehicles", SectorClass.AUTO),
    ("Consumer Discretionary", "2/3 Wheelers", SectorClass.AUTO),
    ("Consumer Discretionary", "Residential Commercial Projects", SectorClass.REALTY),
    ("Consumer Discretionary", "Diversified Retail", SectorClass.GENERIC),
    ("Telecommunication", "Telecom - Cellular & Fixed line services", SectorClass.GENERIC),
    ("Services", "Port & Port services", SectorClass.GENERIC),
    (None, None, None),
    ("", "", None),
])
def test_class_for_metadata(sector, industry, expected):
    assert class_for_metadata(sector, industry) == expected


# --- the routing chain (uses the generated config) --------------------------------

def test_index_routing_still_wins_over_metadata():
    """SBIN is both NIFTY BANK and metadata-bank; index routing answers first."""
    assert sector_class_for("SBIN") == SectorClass.BFSI


def test_realty_index_routes():
    assert sector_class_for("DLF") == SectorClass.REALTY


def test_long_tail_routes_via_metadata(monkeypatch):
    """A symbol in no sectoral index resolves via the metadata map."""
    from nse_data.market import sector_map
    monkeypatch.setattr(sector_map, "sector_for", lambda s, path=None: None)
    monkeypatch.setattr(sector_map, "metadata_class_for", lambda s, path=None: "generic")
    assert sector_class_for("SOMENEWCO") == SectorClass.GENERIC


def test_unrouted_financial_stays_unknown(monkeypatch):
    from nse_data.market import sector_map
    monkeypatch.setattr(sector_map, "sector_for", lambda s, path=None: None)
    monkeypatch.setattr(sector_map, "metadata_class_for", lambda s, path=None: None)
    assert sector_class_for("SOMENBFC") == SectorClass.UNKNOWN


def test_stale_config_class_value_degrades_to_unknown(monkeypatch):
    from nse_data.market import sector_map
    monkeypatch.setattr(sector_map, "sector_for", lambda s, path=None: None)
    monkeypatch.setattr(sector_map, "metadata_class_for", lambda s, path=None: "not_a_class")
    assert sector_class_for("X") == SectorClass.UNKNOWN


# --- the GENERIC verdict ------------------------------------------------------------

MISS = {"yoy_pat_pct": 4.0, "yoy_ebitda_pct": -7.0, "yoy_revenue_pct": 6.0,
        "yoy_other_income_pct": 40.0, "yoy_pbt_pct": -3.0, "yoy_tax_pct": -15.0}
BEAT = {"yoy_pat_pct": 12.0, "yoy_ebitda_pct": 10.0, "yoy_revenue_pct": 9.0}


@pytest.fixture
def generic_symbol(monkeypatch):
    from nse_data.market import sector_map
    monkeypatch.setattr(sector_map, "sector_for", lambda s, path=None: None)
    monkeypatch.setattr(sector_map, "metadata_class_for", lambda s, path=None: "generic")
    return "CEMENTCO"


def test_generic_low_quality_beat_shorts_with_honesty_flag(generic_symbol):
    v = classify_result(generic_symbol, MISS)
    assert v.direction == "short" and v.label == "low"
    assert "low_quality_beat" in v.flags
    assert "other_income_propped" in v.flags
    assert "sector_generic" in v.flags        # the honesty marker


def test_generic_clean_beat_is_long_with_flag(generic_symbol):
    v = classify_result(generic_symbol, BEAT)
    assert v.direction == "long" and v.label == "high"
    assert "sector_generic" in v.flags


def test_generic_neutral_carries_no_noise_flag(generic_symbol):
    v = classify_result(generic_symbol, {"yoy_pat_pct": 1.0, "yoy_ebitda_pct": 0.5})
    assert v.label == "neutral"
    assert "sector_generic" not in v.flags


def test_generic_narrative_still_folds(generic_symbol):
    """Guidance cut applies to the long tail too (non-BFSI rule)."""
    flat = {"yoy_pat_pct": 2.0, "yoy_ebitda_pct": 0.5}
    v = classify_result(generic_symbol, flat, narrative={"guidance": "cut"})
    assert v.direction == "short"
    assert "guidance_cut" in v.flags