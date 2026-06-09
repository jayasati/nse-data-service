"""Unit tests for the vision-first extractor's offline logic (no API calls).

Covers the parts that don't need the LLM: canonical mapping / unit conversion,
units detection, P&L page location, accounting-identity validations, confidence,
and that extract() is free (no LLM) when use_llm_fallback is False.
"""
from __future__ import annotations

import pytest

from nse_data.parsers import financial_extractor as fe
from nse_data.parsers.extractors import vision_financial as vf


# --------------------------------------------------------------------------- #
# canonical mapping & unit conversion
# --------------------------------------------------------------------------- #

def test_map_block_applies_unit_factor_to_amounts_not_eps():
    block = {
        "revenue": "2,54,972", "pat": "27,367", "tax": "(9,347)",
        "eps_basic": 41.49, "eps_diluted": "41.43",
    }
    out = vf._map_block(block, factor=0.01)   # source in lakh -> crore
    assert out["revenue_cr"] == pytest.approx(2549.72)
    assert out["pat_cr"] == pytest.approx(273.67)
    assert out["tax_cr"] == pytest.approx(-93.47)   # parentheses -> negative
    assert out["eps_basic"] == 41.49          # EPS never scaled
    assert out["eps_diluted"] == 41.43


def test_coerce_number_formats():
    assert vf._coerce_number("1,234.5") == 1234.5
    assert vf._coerce_number("(166.79)") == -166.79
    assert vf._coerce_number("₹ 2,54,972") == 254972.0
    assert vf._coerce_number(None) is None
    assert vf._coerce_number("nil") is None
    assert vf._coerce_number("-") is None


def test_units_factor():
    assert vf.units_factor("INR crore") == 1.0
    assert vf.units_factor("Rs in Million") == 0.1
    assert vf.units_factor("in lakhs") == 0.01
    assert vf.units_factor("in thousands") == 1e-4
    assert vf.units_factor(None) == 1.0
    assert vf.units_factor("rupees") == 1.0


def test_to_result_shape():
    data = {
        "standalone": {"revenue": 100, "pat": 20},
        "consolidated": {"revenue": 300, "pat": 50},
        "units_in_source_pdf": "INR crore",
        "period_ending": "2026-03-31",
        "table_found": True,
    }
    r = vf._to_result(data, cost_usd=0.01)
    assert r["fields"]["revenue_cr"] == 100.0
    assert r["consolidated"]["pat_cr"] == 50.0
    assert r["units_phrase"] == "INR crore"
    assert r["period_ending"] == "2026-03-31"
    assert r["table_found"] is True


def test_to_result_null_consolidated():
    data = {"standalone": {"revenue": 100}, "consolidated": None,
            "units_in_source_pdf": "INR lakh", "table_found": True}
    r = vf._to_result(data, 0.0)
    assert r["consolidated"] == {}
    assert r["fields"]["revenue_cr"] == 1.0   # 100 lakh = 1 crore


# --------------------------------------------------------------------------- #
# orchestrator: units, page location, validations, confidence
# --------------------------------------------------------------------------- #

def test_detect_units():
    assert fe._detect_units("Figures are Rs. in Crore") == (1.0, "in crore")
    f, p = fe._detect_units("All amounts in lakhs unless stated")
    assert f == 0.01 and p == "in lakh"
    assert fe._detect_units("no unit phrase here") == (1.0, None)


def test_locate_pnl_pages_picks_anchor_page_and_successor():
    pages = [
        "Cover letter to the stock exchange. Outcome of board meeting.",
        "Statement of Profit and Loss\nRevenue from operations 100\n"
        "Total income 110\nProfit before tax 30\nTax expense 8\n"
        "Profit for the period 22\nEarnings per share 4.2",
        "continuation: Total comprehensive income 23",
        "Segment information unrelated page",
    ]
    idx = fe._locate_pnl_pages(pages)
    assert 1 in idx          # the P&L page
    assert 2 in idx          # its successor (continuation)
    assert 0 not in idx


def test_locate_pnl_pages_empty_when_no_anchors():
    assert fe._locate_pnl_pages(["nothing", "to see here"]) == []


def test_validations_flag_broken_identities():
    # PBT - tax != PAT, and total_income != revenue + other_income
    bad = {
        "revenue_cr": 100.0, "other_income_cr": 10.0, "total_income_cr": 999.0,
        "total_expenses_cr": 80.0, "pbt_cr": 30.0, "tax_cr": 8.0, "pat_cr": 5.0,
    }
    warns = fe._run_validations(bad)
    assert any("Total income" in w for w in warns)
    assert any("PBT - tax" in w for w in warns)


def test_validations_clean_on_consistent_numbers():
    good = {
        "revenue_cr": 100.0, "other_income_cr": 10.0, "total_income_cr": 110.0,
        "total_expenses_cr": 80.0, "pbt_cr": 30.0, "tax_cr": 8.0, "pat_cr": 22.0,
    }
    assert fe._run_validations(good) == []


def test_confidence_rewards_core_and_penalizes_warnings():
    good = {"revenue_cr": 1, "pat_cr": 1, "total_income_cr": 1, "pbt_cr": 1}
    assert fe._confidence(good, []) >= 0.90
    assert fe._confidence(good, ["w1"]) < fe._confidence(good, [])
    assert fe._confidence({}, []) == 0.0


# --------------------------------------------------------------------------- #
# gap-fill merge: a sparse vision read is backfilled from the text path
# --------------------------------------------------------------------------- #

def _patch_pdf_io(monkeypatch):
    monkeypatch.setattr(fe.pdf_text, "page_texts",
                        lambda data: ["Revenue from operations ... profit before tax"])
    monkeypatch.setattr(fe.pdf_render, "render_pages", lambda *a, **k: [b"img"])


def test_incomplete_vision_is_gapfilled_from_text(monkeypatch):
    _patch_pdf_io(monkeypatch)
    # vision read only revenue/pbt/pat (the BEL case)
    monkeypatch.setattr(fe, "_vision", lambda images, **ctx: {
        "fields": {"revenue_cr": 100.0, "pbt_cr": 25.0, "pat_cr": 20.0},
        "consolidated": {}, "units_phrase": "INR crore",
        "period_ending": "2026-03-31", "cost_usd": 0.005,
    })
    # text path has the full set (incl. a different revenue, which must NOT win)
    monkeypatch.setattr(fe, "_text_llm", lambda text, **ctx: {
        "fields": {"revenue_cr": 999.0, "other_income_cr": 5.0, "total_income_cr": 105.0,
                   "total_expenses_cr": 80.0, "pbt_cr": 25.0, "tax_cr": 5.0,
                   "pat_cr": 20.0, "eps_basic": 3.0, "eps_diluted": 3.0},
        "consolidated": {}, "units_phrase": "INR crore",
        "period_ending": "2026-03-31", "cost_usd": 0.01,
    })
    r = fe.extract("x.pdf", data=b"x", use_llm_fallback=True)
    assert r.strategy == "vision+text"
    assert r.fields["revenue_cr"] == 100.0          # vision wins where present
    assert r.fields["other_income_cr"] == 5.0       # filled from text
    assert r.fields["total_income_cr"] == 105.0     # filled
    assert r.fields["tax_cr"] == 5.0                # filled
    assert r.fields["eps_basic"] == 3.0             # filled
    assert r.llm_cost_usd == pytest.approx(0.015)   # both calls paid


def test_complete_vision_skips_text(monkeypatch):
    _patch_pdf_io(monkeypatch)
    monkeypatch.setattr(fe, "_vision", lambda images, **ctx: {
        "fields": {"revenue_cr": 100.0, "other_income_cr": 5.0, "total_income_cr": 105.0,
                   "total_expenses_cr": 80.0, "pbt_cr": 25.0, "tax_cr": 5.0, "pat_cr": 20.0},
        "consolidated": {}, "units_phrase": "INR crore",
        "period_ending": "2026-03-31", "cost_usd": 0.016,
    })
    def _boom(*a, **k):
        raise AssertionError("text path must not run when vision is complete")
    monkeypatch.setattr(fe, "_text_llm", _boom)
    r = fe.extract("x.pdf", data=b"x", use_llm_fallback=True)
    assert r.strategy == "vision"
    assert r.llm_cost_usd == pytest.approx(0.016)


def test_extract_is_free_without_llm_flag(tmp_path):
    # A non-PDF file: page_texts fails gracefully; with the flag off we must not
    # touch the LLM and must return a no-cost llm_disabled result.
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4 not really a pdf")
    r = fe.extract(str(p), use_llm_fallback=False)
    assert r.strategy == "llm_disabled"
    assert r.llm_cost_usd == 0.0
    assert r.fields == {}
