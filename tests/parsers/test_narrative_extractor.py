"""P7 narrative extractor (playbook §4) — phrasing taken from real filings
(L&T Q2 FY26 order inflow, ITC dividend style, Infosys guidance style)."""
from __future__ import annotations

from nse_data.parsers.narrative import NarrativeFields, extract_narrative


def test_empty_text_is_all_none():
    f = extract_narrative("")
    assert f == NarrativeFields()
    assert f.as_dict()["guidance"] is None


# --- guidance (IT) -------------------------------------------------------------

def test_guidance_raised():
    f = extract_narrative(
        "The company raised its FY26 revenue growth guidance to 2%-3% in constant currency."
    )
    assert f.guidance == "raised"


def test_guidance_revised_upwards():
    assert extract_narrative(
        "Revenue guidance for FY26 revised upwards to 3.5%."
    ).guidance == "raised"


def test_guidance_cut():
    assert extract_narrative(
        "Given demand uncertainty, the Board lowered the full-year guidance to 1%-2%."
    ).guidance == "cut"


def test_guidance_maintained():
    assert extract_narrative(
        "The company maintained its margin guidance of 20%-22% for the year."
    ).guidance == "maintained"


def test_no_guidance_mention():
    assert extract_narrative("Revenue grew 5% during the quarter.").guidance is None


def test_directional_guidance_beats_boilerplate_maintained():
    f = extract_narrative(
        "Margin guidance maintained at 20%-22%. The company raised its revenue guidance to 3%."
    )
    assert f.guidance == "raised"


# --- volume growth (FMCG / auto) ------------------------------------------------

def test_underlying_volume_growth():
    assert extract_narrative(
        "The quarter saw underlying volume growth of 4.5% with improving rural demand."
    ).volume_growth == 4.5


def test_volume_decline_is_negative():
    assert extract_narrative(
        "Volumes declined by 3% on a high base."
    ).volume_growth == -3.0


def test_uvg_abbreviation():
    assert extract_narrative("UVG for the quarter was 6%.").volume_growth == 6.0


# --- order inflow (capital goods) ------------------------------------------------

def test_order_inflow_lakh_crore_grouping():
    # Real L&T Q2 FY26 phrasing & Indian digit grouping.
    f = extract_narrative(
        "The group received order inflow of ₹ 1,15,784 crore during the quarter, "
        "a growth of 45% over the corresponding quarter."
    )
    assert f.order_inflow == 115784.0


def test_orders_worth():
    assert extract_narrative(
        "The company secured orders worth Rs. 8,000 crore in the defence segment."
    ).order_inflow == 8000.0


# --- FDA status (pharma) ----------------------------------------------------------

def test_fda_warning_letter():
    assert extract_narrative(
        "The USFDA issued a warning letter for the Halol facility."
    ).fda_status == "warning_letter"


def test_fda_import_alert_outranks_observation():
    f = extract_narrative(
        "The facility received Form 483 with three observations; subsequently "
        "the USFDA placed the unit under import alert."
    )
    assert f.fda_status == "import_alert"


def test_fda_clean_eir():
    assert extract_narrative(
        "The company received the Establishment Inspection Report (EIR) for its "
        "Goa plant, closing the inspection."
    ).fda_status == "clean"


def test_no_fda_mention():
    assert extract_narrative("Revenue grew 12% led by US generics.").fda_status is None


# --- dividend ---------------------------------------------------------------------

def test_interim_dividend_per_share():
    assert extract_narrative(
        "The Board declared an interim dividend of ₹16.00 per equity share."
    ).dividend == 16.0


def test_dividend_rs_slash_style():
    assert extract_narrative(
        "Interim dividend of Rs. 5/- per share for FY 2025-26."
    ).dividend == 5.0


# --- management tone (§3 step 5) ----------------------------------------------------

def test_positive_tone():
    f = extract_narrative(
        "We see strong demand across segments; a robust pipeline and margin "
        "expansion support the outlook."
    )
    assert f.mgmt_tone == "positive"


def test_negative_tone():
    f = extract_narrative(
        "The environment remains challenging with weak demand, continued margin "
        "pressure and macro headwinds."
    )
    assert f.mgmt_tone == "negative"


def test_mixed_or_absent_tone_is_none():
    assert extract_narrative("Results were in line with the prior year.").mgmt_tone is None
    assert extract_narrative(
        "Strong demand in autos was offset by weak demand in exports."
    ).mgmt_tone is None
