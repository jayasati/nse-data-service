"""BFSI extraction hardening (Week 17.5 follow-up): identity corrections,
comparative plausibility guard, NPA text override, stored-history growth.

These lock in the fixes made after the live SBI extraction misread the dense
10-column bank P&L — without them the quality verdict misses flags.
"""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.parsers import financial_extractor as fe
from nse_data.parsers.extractors import vision_financial as vf
from nse_data.fundamentals import from_results as fr
from nse_data.storage.db import apply_migrations


def test_identity_correction_fixes_nii_and_other_income():
    # model misread NII / other_income but read the components right
    blk = {"revenue": 123097.67, "total_income": 140411.77, "other_income": 3840.0,
           "interest_expended": 78717.67, "net_interest_income": 58696.0}
    vf._correct_block_identities(blk)
    assert abs(blk["other_income"] - 17314.10) < 0.5     # = total_income - revenue
    assert abs(blk["net_interest_income"] - 44380.0) < 0.5  # = int_earned - int_expended


def test_plausibility_guard_drops_full_year_comparative():
    assert vf._plausible_comparative(27704, 31286) == 31286     # a real quarter → kept
    assert vf._plausible_comparative(27704, 118421) is None      # full-year column → dropped


def test_growth_skips_implausible_comparative():
    block = {"operating_profit": 27704, "pat": 19684,
             "prev_quarter": {"operating_profit": 118421, "pat": 21028},   # full-year → bogus
             "year_ago_quarter": {"operating_profit": 31286, "pat": 18642}}
    g = vf._growth_from_block(block)
    assert "qoq_ppop_pct" not in g                 # implausible prev-Q comparative dropped
    assert round(g["yoy_ppop_pct"], 2) == -11.45   # plausible year-ago kept


def test_npa_text_override_parses_ocr_noise():
    txt = "(c) % of gross NPAS 1 49o/o 'l 570/0 1 82%\n(d) % of net NPAS 0 39% 0 39%"
    assert fe._pct_after(txt, "% of gross npa", "of gross npa") == 1.49
    assert fe._pct_after(txt, "% of net npa", "of net npa") == 0.39
    vis = {"fields": {"gross_npa_pct": 2.78, "net_npa_pct": 0.67}}   # model misread
    fe._apply_bfsi_text_overrides(vis, txt)
    assert vis["fields"]["gross_npa_pct"] == 1.49
    assert vis["fields"]["net_npa_pct"] == 0.39


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c, "migrations")
    yield c
    c.close()


def test_pdf_text_growth_recovers_comparatives_from_filing():
    """The PDF carries all comparative columns; growth is read from it (no history).

    Value-anchor recovers PPOP (clean row); label-anchor recovers other-income
    (current cell OCR-garbled, but its label + year-ago column survive)."""
    from pathlib import Path

    from nse_data.parsers import pdf_text

    pdf = (Path(__file__).parent / "fixtures" / "pdfs"
           / "sbin_q4fy26_5808055808055808.pdf")
    if not pdf.exists():
        pytest.skip("SBI fixture PDF not present")
    full = "\n".join(pdf_text.page_texts(pdf.read_bytes()))
    # the model's reliable current-quarter reads
    fields = dict(operating_profit_cr=27704.18, pat_cr=19683.75,
                  other_income_cr=17314.10, revenue_cr=123097.67)
    g = fe._pdf_text_growth(full, fields)
    assert round(g["yoy_ppop_pct"], 2) == -11.45
    assert round(g["qoq_ppop_pct"], 2) == -15.70
    assert round(g["yoy_pat_pct"], 2) == 5.58
    assert g["yoy_other_income_pct"] < -25     # ~-28.9%, drives treasury_hit


def test_quarter_growth_from_stored_history_bfsi(conn):
    # the reliable path: compare clean current-quarter reads across stored quarters
    import time
    fr.persist_extraction(conn, symbol="SBIN", period_ending="2025-03-31", scope="standalone",
        fields=dict(operating_profit_cr=31286.04, other_income_cr=24366.67, provisions_cr=6441.69,
                    pat_cr=18642.59, revenue_cr=119509.39),
        units_phrase="INR crore", confidence=0.95, strategy="vision",
        source_fingerprint="ly", broadcast_dt="2025-03-31", growth=None, now=int(time.time()) - 100)
    fr.persist_extraction(conn, symbol="SBIN", period_ending="2026-03-31", scope="standalone",
        fields=dict(operating_profit_cr=27704.18, other_income_cr=17314.10, provisions_cr=2872.16,
                    pat_cr=19683.75, revenue_cr=123097.67),
        units_phrase="INR crore", confidence=1.0, strategy="vision",
        source_fingerprint="cur", broadcast_dt="2026-03-31", growth=None, now=int(time.time()))
    g = fr.quarter_growth(conn, "SBIN", "2026-03-31", "standalone")
    assert round(g["yoy_ppop_pct"], 2) == -11.45
    assert round(g["yoy_pat_pct"], 2) == 5.58
    assert round(g["yoy_other_income_pct"], 2) == -28.94
    assert g["yoy_provisions_pct"] < -30
