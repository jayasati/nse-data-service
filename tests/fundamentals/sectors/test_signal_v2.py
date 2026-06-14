"""Sector Signal Engine v2 — confidence gating, metrics, KPI surfacing.

These cover the additive axes (confidence / metrics / kpi_signals / tradable)
that wrap every verdict via ``enrich_signal``, and the guarantee that the
enrichment never changes a sector rule's label/direction (the nine validated
regressions stay intact).
"""
from __future__ import annotations

from nse_data.fundamentals.earnings_quality import QualityVerdict
from nse_data.fundamentals.sectors import classify_result
from nse_data.fundamentals.sectors.base import SectorClass, enrich_signal


def test_verdict_carries_v2_axes():
    v = QualityVerdict()
    d = v.as_dict()
    assert d["confidence"] == "medium"
    assert d["metrics"] == {} and d["kpi_signals"] == []
    assert d["tradable"] is False          # neutral / no direction


def test_clean_operating_beat_is_medium_and_tradable():
    # EBITDA operating line present and up → genuine long, medium confidence.
    g = {"yoy_ebitda_pct": 10.0, "yoy_pat_pct": 12.0, "yoy_revenue_pct": 8.0}
    v = classify_result("JSWSTEEL", g)
    assert v.direction == "long"
    assert v.confidence == "medium"
    assert v.tradable is True
    assert v.metrics["operating_growth_pct"] == 10.0


def test_revenue_proxy_only_is_low_confidence_not_tradable():
    # No operating line — only the weak revenue proxy. Directional but NOT
    # tradable: the confidence gate keeps half-visible reads context-only.
    g = {"yoy_revenue_pct": 10.0, "yoy_pat_pct": 12.0}
    v = classify_result("JSWSTEEL", g)
    assert v.direction == "long"
    assert v.confidence == "low"
    assert v.tradable is False


def test_consensus_surprise_lifts_to_high():
    g = {"yoy_ebitda_pct": 10.0, "yoy_pat_pct": 12.0}
    v = classify_result("JSWSTEEL", g, surprise={"surprise_basis": "consensus"})
    assert v.confidence == "high" and v.tradable is True


def test_metals_kpi_surfaced_and_confident():
    g = {"yoy_ebitda_pct": 39.6, "yoy_pat_pct": 300.0}
    v = classify_result("JSWSTEEL", g, narrative={"ebitda_per_tonne": 11000})
    assert "ebitda_per_tonne=11000" in v.kpi_signals
    assert v.confidence == "high"          # a corroborating sector KPI on a live op line
    assert v.metrics.get("kpi_ebitda_per_tonne") == 11000


def test_unbuilt_sector_is_low_confidence_with_metrics():
    v = classify_result("SOME_UNLISTED_XYZ", {"yoy_revenue_pct": 5.0})
    assert v.confidence == "low" and v.tradable is False
    assert "operating_line" in v.metrics    # audit trail still populated


def test_enrich_never_changes_label_or_direction():
    g = {"yoy_ebitda_pct": -10.0, "yoy_pat_pct": 5.0}   # low-quality beat → short
    base = classify_result("JSWSTEEL", g)
    label, direction = base.label, base.direction
    again = enrich_signal(
        QualityVerdict(label=label, direction=direction),
        SectorClass.METALS, g)
    assert (again.label, again.direction) == (label, direction)
