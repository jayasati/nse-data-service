"""Tests for the Week-14 fundamental quality score + its gate/confidence wiring."""

from __future__ import annotations

import sqlite3

from nse_data.fundamentals import quality_score as qs
from nse_data.signals.confidence import BASE_SCORE, score_confidence
from nse_data.signals.detect import _hard_gated


# ---- pure scoring ----------------------------------------------------------

def test_strong_company_scores_high():
    m = {"revenue_growth": 20, "roce": 25, "roe": 18, "de": 0.2,
         "promoter_holding": 60, "pe": 15}
    out = qs.compute_quality_score(m, sector_pe_avg=25)
    assert out["quality_score"] == 100.0
    assert out["loss_making"] == 0 and out["high_debt"] == 0


def test_weak_company_scores_low_and_flags():
    out = qs.compute_quality_score({"roce": 5, "de": 3, "roe": -2}, sector_pe_avg=20)
    assert out["quality_score"] < 30
    assert out["loss_making"] == 1 and out["high_debt"] == 1


def test_no_data_scores_none():
    assert qs.compute_quality_score({})["quality_score"] is None


def test_partial_data_still_scores():
    # only P/E available vs sector -> a score, not None
    out = qs.compute_quality_score({"pe": 10}, sector_pe_avg=20)
    assert out["quality_score"] is not None


def test_pledge_deduction():
    base = qs.compute_quality_score({"roce": 25, "roe": 18}, sector_pe_avg=None)
    pledged = qs.compute_quality_score(
        {"roce": 25, "roe": 18, "pledge": 30}, sector_pe_avg=None)
    assert pledged["quality_score"] < base["quality_score"]


# ---- gate (task 14.4) ------------------------------------------------------

def test_quality_gate_kills_below_30():
    args = (None, {"AAA": 100}, set())     # series, listing_bars, blacklist
    assert _hard_gated("AAA", *args, {"AAA": 25}) is True     # < 30 -> kill
    assert _hard_gated("AAA", *args, {"AAA": 55}) is False    # ok
    assert _hard_gated("AAA", *args, {}) is False             # no score -> no kill


# ---- confidence (task 14.5) ------------------------------------------------

def test_quality_confidence_contribution():
    assert score_confidence({}, None, quality_score=80) == BASE_SCORE + 0.10
    assert score_confidence({}, None, quality_score=60) == BASE_SCORE + 0.05
    assert score_confidence({}, None, quality_score=40) == BASE_SCORE
    assert score_confidence({}, None, quality_score=20) == BASE_SCORE - 0.15
    assert score_confidence({}, None, quality_score=None) == BASE_SCORE


# ---- nightly pass ----------------------------------------------------------

def test_run_quality_pass_persists():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE raw_fundamentals_screener (symbol TEXT, as_of_date TEXT,
            roce REAL, roe REAL, stock_pe REAL, market_cap REAL,
            debt_to_equity REAL, sales_cagr_3y REAL);
        CREATE TABLE raw_quote_metadata (symbol TEXT, pe_ratio REAL,
            market_cap_cr REAL, sector TEXT);
        CREATE TABLE stock_fundamentals (symbol TEXT PRIMARY KEY, quality_score REAL,
            revenue_growth_yoy REAL, roe REAL, roce REAL, debt_equity REAL,
            pe_ratio REAL, market_cap REAL, promoter_holding REAL,
            promoter_pledge REAL, loss_making INT, high_debt INT, updated_date TEXT);
        INSERT INTO raw_fundamentals_screener VALUES
            ('GOOD','2026-06-01', 25, 18, 15, 50000, 0.2, 20);
        INSERT INTO raw_quote_metadata VALUES ('GOOD', 15, 50000, 'IT');
    """)
    conn.commit()
    rep = qs.run_quality_pass(conn, ["GOOD"])
    assert rep["symbols"] == 1 and rep["scored"] == 1
    score = conn.execute(
        "SELECT quality_score FROM stock_fundamentals WHERE symbol='GOOD'"
    ).fetchone()[0]
    assert score and score > 70
