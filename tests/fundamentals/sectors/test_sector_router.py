"""Sector router regressions (SECTOR_RESULT_PLAYBOOK.md).

Two guarantees the per-sector refactor must hold:
  1. BFSI is unchanged — SBI's hidden miss still shorts (no regression on the
     first built sector).
  2. A sector with no built rule is guarded — it is always routed to an
     out-of-scope neutral, whatever its draft rule says, so a confident verdict
     can never leak from a sector the engine cannot yet read.

Energy-specific behaviour (the ONGC SHORT) lives in test_energy_ongc.py.
"""
from __future__ import annotations

from nse_data.fundamentals.sectors import SectorClass, classify_result, spec_for
from nse_data.fundamentals.sectors.base import out_of_scope_verdict
from nse_data.fundamentals.sectors.bfsi import SPEC as BFSI_SPEC

# SBI Q4 FY26 — PAT +5.6% beat hides PPOP −11.45% miss, provisions propping.
SBI_GROWTH = {"yoy_pat_pct": 5.6, "yoy_ppop_pct": -11.45, "yoy_provisions_pct": -36.6}
SBI_FIELDS = {"profit_on_sale_of_investments_cr": -1471.0}


def test_bfsi_built_and_still_shorts_sbi():
    """The first built sector keeps its validated verdict."""
    assert BFSI_SPEC.built is True
    v = BFSI_SPEC.classify(SBI_GROWTH, SBI_FIELDS)
    assert v.direction == "short"
    assert "low_quality_beat" in v.flags


def test_unbuilt_sector_routes_to_out_of_scope():
    """A symbol with no routable class is downgraded to an explicit neutral,
    so the engine never emits a confident verdict for it. With realty +
    GENERIC built, the guard's remaining (and intentional) population is
    non-bank financials — the lender guard keeps them unrouted because the
    generic EBITDA derivation is wrong for a lender."""
    spec = spec_for("ANGELONE")            # broker — lender guard → UNKNOWN
    assert spec.sector_class == SectorClass.UNKNOWN
    assert spec.built is False
    v = classify_result("ANGELONE", {"yoy_pat_pct": 10.0, "yoy_revenue_pct": -8.0})
    assert v.label == "neutral"
    assert v.direction is None
    assert "sector_out_of_scope" in v.flags


def test_all_registered_sectors_are_built():
    """P5 complete: every SectorClass in the registry carries a validated rule
    (each backed by a real-PDF regression in this directory)."""
    from nse_data.fundamentals.sectors import REGISTRY

    for klass, spec in REGISTRY.items():
        assert spec.built is True, f"{klass.value} lost its built rule"


def test_out_of_scope_verdict_is_neutral():
    v = out_of_scope_verdict(SectorClass.METALS)
    assert v.label == "neutral" and v.direction is None


def test_spec_for_unknown_symbol_is_safe():
    """An unmappable symbol resolves to a safe, never-confident UNKNOWN spec."""
    spec = spec_for("__definitely_not_a_symbol__")
    assert spec.sector_class == SectorClass.UNKNOWN
    assert spec.built is False
