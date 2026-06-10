"""P7 narrative → verdict folding (playbook §4, base.apply_narrative).

The discipline under test: a narrative signal must be unambiguous to move the
verdict, and the only narrative that shorts *against* a healthy operating line
is a pharma FDA warning letter / import alert. Guidance — the dominant IT
signal — shorts a non-beating quarter, neutralises a mixed one, and upgrades a
clean flat one; the FMCG/auto price-led-volume prop caps a long but never
shorts alone.
"""
from __future__ import annotations

from nse_data.fundamentals.sectors import classify_result

# A flat-but-clean IT operating quarter (no P&L verdict either way).
FLAT_IT = {"yoy_pat_pct": 3.0, "yoy_ebitda_pct": 0.5, "yoy_revenue_pct": 4.0}
# A genuine IT operating beat.
BEAT_IT = {"yoy_pat_pct": 12.0, "yoy_ebitda_pct": 9.0, "yoy_revenue_pct": 8.0}
# A clean pharma beat.
BEAT_PH = {"yoy_pat_pct": 15.0, "yoy_ebitda_pct": 11.0, "yoy_revenue_pct": 10.0}


def test_no_narrative_is_a_no_op():
    assert classify_result("INFY", FLAT_IT).label == "neutral"
    assert classify_result("INFY", FLAT_IT, narrative=None).label == "neutral"
    assert classify_result("INFY", FLAT_IT, narrative={}).label == "neutral"


def test_guidance_cut_on_flat_quarter_shorts():
    """The IT print that turns on guidance: P&L flat (would read neutral), but
    the narrative cuts guidance → the market sells the cut, engine must too."""
    v = classify_result("INFY", FLAT_IT, narrative={"guidance": "cut"})
    assert v.direction == "short"
    assert v.label == "low"
    assert "guidance_cut" in v.flags


def test_guidance_cut_on_operating_beat_is_mixed_neutral():
    """A beat + a cut is a mixed print — downgraded from long, never shorted."""
    v = classify_result("INFY", BEAT_IT, narrative={"guidance": "cut"})
    assert v.direction is None
    assert v.label == "neutral"
    assert "guidance_cut" in v.flags


def test_guidance_raised_upgrades_clean_flat_quarter():
    v = classify_result("INFY", FLAT_IT, narrative={"guidance": "raised"})
    assert v.direction == "long"
    assert v.label == "high"
    assert "guidance_raised" in v.flags


def test_guidance_raised_never_rescues_an_operating_miss():
    miss = {"yoy_pat_pct": 5.0, "yoy_ebitda_pct": -6.0, "yoy_other_income_pct": 30.0}
    v = classify_result("INFY", miss, narrative={"guidance": "raised"})
    assert v.direction == "short"          # the operating miss stands
    assert "low_quality_beat" in v.flags
    assert "guidance_raised" in v.flags    # surfaced, but not a rescue


def test_guidance_raised_does_not_upgrade_a_propped_quarter():
    """Maruti shape + a raise: tax-flattered flat quarter stays neutral."""
    propped = {"yoy_pat_pct": 7.3, "yoy_ebitda_pct": 0.4, "yoy_revenue_pct": 13.2,
               "yoy_pbt_pct": -16.7, "yoy_tax_pct": -52.8, "yoy_other_income_pct": -38.1}
    v = classify_result("MARUTI", propped, narrative={"guidance": "raised"})
    assert v.direction is None
    assert "tax_propped" in v.flags


def test_fda_negative_shorts_even_a_clean_beat():
    """§2.6: a warning letter / import alert is very negative regardless of the
    P&L — the one narrative signal that overrides a healthy operating line."""
    for status in ("warning_letter", "import_alert"):
        v = classify_result("CIPLA", BEAT_PH, narrative={"fda_status": status})
        assert v.direction == "short", status
        assert v.label == "low"
        assert "fda_negative" in v.flags


def test_fda_clean_or_observation_does_not_short():
    for status in ("clean", "observation", None):
        v = classify_result("CIPLA", BEAT_PH, narrative={"fda_status": status})
        assert v.direction == "long", status


def test_fda_only_applies_to_pharma():
    """A 'warning letter' phrase in a non-pharma filing must not short it."""
    v = classify_result("INFY", BEAT_IT, narrative={"fda_status": "warning_letter"})
    assert v.direction == "long"
    assert "fda_negative" not in v.flags


def test_price_led_volume_caps_fmcg_long_to_neutral():
    """§2.4: revenue up on price with volumes flat/down — the headline beat is
    demand-free; cap the long, don't short."""
    g = {"yoy_pat_pct": 8.0, "yoy_ebitda_pct": 6.0, "yoy_revenue_pct": 9.0}
    v = classify_result("ITC", g, narrative={"volume_growth": -1.0})
    assert v.direction is None
    assert v.label == "neutral"
    assert "price_led_growth" in v.flags


def test_healthy_volume_growth_leaves_long_alone():
    g = {"yoy_pat_pct": 8.0, "yoy_ebitda_pct": 6.0, "yoy_revenue_pct": 9.0}
    v = classify_result("ITC", g, narrative={"volume_growth": 4.5})
    assert v.direction == "long"
    assert "price_led_growth" not in v.flags


def test_bfsi_ignores_guidance():
    """Banks don't guide in result filings — a stray 'guidance' phrase must not
    move a BFSI verdict."""
    clean = {"yoy_pat_pct": 9.0, "yoy_ppop_pct": 5.0, "yoy_provisions_pct": -2.0}
    v = classify_result("SBIN", clean, narrative={"guidance": "cut"})
    assert v.direction == "long"
    assert "guidance_cut" not in v.flags
