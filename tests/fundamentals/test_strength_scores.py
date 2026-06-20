"""Tests for the R7 balance-sheet + R8 Piotroski/distress conviction screens."""
from __future__ import annotations

import sqlite3

from nse_data.fundamentals import strength_scores as ss


# ---- R7 ratios -------------------------------------------------------------

def test_interest_coverage():
    assert ss.interest_coverage({"pbt_cr": 90.0, "finance_cost_cr": 10.0}) == 10.0  # (90+10)/10
    assert ss.interest_coverage({"pbt_cr": 90.0, "finance_cost_cr": 0}) is None     # debt-free
    assert ss.interest_coverage({"finance_cost_cr": 10.0}) is None                  # no pbt


def test_current_ratio_and_de():
    assert ss.current_ratio({"current_assets_cr": 300, "current_liabilities_cr": 200}) == 1.5
    assert ss.debt_to_equity({"borrowings_cr": 50, "equity_cr": 200}) == 0.25
    assert ss.debt_to_equity({"borrowings_cr": 50, "equity_cr": -10}) is None       # neg equity → flag


def test_balance_sheet_score_strong_vs_weak():
    strong = ss.balance_sheet_score(
        {"pbt_cr": 90, "finance_cost_cr": 5, "current_assets_cr": 300,
         "current_liabilities_cr": 100, "borrowings_cr": 20, "equity_cr": 400, "cfo_cr": 80})
    weak = ss.balance_sheet_score(
        {"pbt_cr": 5, "finance_cost_cr": 20, "current_assets_cr": 80,
         "current_liabilities_cr": 100, "borrowings_cr": 300, "equity_cr": 100, "cfo_cr": -10})
    assert strong > 80 and weak < 25


def test_balance_sheet_score_partial_data():
    # only current ratio available → still a score, not None
    assert ss.balance_sheet_score({"current_assets_cr": 300, "current_liabilities_cr": 100}) is not None
    assert ss.balance_sheet_score({}) is None


# ---- R8 Piotroski ----------------------------------------------------------

def test_piotroski_full_nine():
    now = {"pat_cr": 120, "cfo_cr": 150, "total_assets_cr": 1000, "revenue_cr": 800,
           "cost_of_materials_cr": 300, "borrowings_cr": 100, "equity_cr": 500,
           "current_assets_cr": 400, "current_liabilities_cr": 200, "eps_basic": 12.0}
    # prior: PAT 100 @ EPS 10 → 10 shares == now's 10 shares (no dilution)
    prior = {"pat_cr": 100, "cfo_cr": 90, "total_assets_cr": 1000, "revenue_cr": 700,
             "cost_of_materials_cr": 320, "borrowings_cr": 150, "equity_cr": 500,
             "current_assets_cr": 300, "current_liabilities_cr": 200, "eps_basic": 10.0}
    f = ss.piotroski_f(now, prior)
    assert f["n_signals"] == 9
    assert f["f_score"] == 9            # improving on every axis, cash-backed, deleveraging
    assert f["signals"]["accrual"] == 1 and f["signals"]["no_dilution"] == 1


def test_piotroski_partial_when_no_prior():
    # no prior period → only the 4 level signals are computable
    now = {"pat_cr": 100, "cfo_cr": 120, "total_assets_cr": 1000}
    f = ss.piotroski_f(now, None)
    assert f["signals"]["roa_pos"] == 1 and f["signals"]["cfo_pos"] == 1
    assert f["signals"]["accrual"] == 1                     # cfo 120 > pat 100
    assert f["signals"]["roa_up"] is None                   # needs prior
    assert f["n_signals"] == 3                              # roa_pos, cfo_pos, accrual


def test_distress_flags():
    # negative equity → D/E undefined, so high_leverage doesn't fire (negative_net_worth covers it)
    flags = ss.distress_flags(
        {"equity_cr": -5, "pbt_cr": 1, "finance_cost_cr": 10, "current_assets_cr": 50,
         "current_liabilities_cr": 100, "borrowings_cr": 400, "cfo_cr": -20, "pat_cr": -30})
    assert set(flags) == {"negative_net_worth", "interest_cover_below_1.5x",
                          "current_ratio_below_1", "negative_cfo", "loss_making"}
    # high_leverage fires on positive equity with borrowings/equity > 3
    assert "high_leverage" in ss.distress_flags({"equity_cr": 100, "borrowings_cr": 400})
    # a clean balance sheet → no flags
    assert ss.distress_flags({"equity_cr": 500, "pbt_cr": 90, "finance_cost_cr": 5,
                              "current_assets_cr": 300, "current_liabilities_cr": 100,
                              "borrowings_cr": 20, "cfo_cr": 80, "pat_cr": 70}) == []


def test_compute_strength_no_data():
    s = ss.compute_strength(None, None)
    assert s["f_score"] is None and s["bs_score"] is None and s["distress"] == []


# ---- reader + nightly pass -------------------------------------------------

_EF = """
CREATE TABLE extracted_financials (
  symbol TEXT, period_ending TEXT, scope TEXT,
  pat_cr REAL, pbt_cr REAL, finance_cost_cr REAL, cfo_cr REAL, revenue_cr REAL,
  cost_of_materials_cr REAL, total_assets_cr REAL, current_assets_cr REAL,
  current_liabilities_cr REAL, total_liabilities_cr REAL, borrowings_cr REAL,
  equity_cr REAL, eps_basic REAL);
CREATE TABLE stock_strength (symbol TEXT PRIMARY KEY, f_score INTEGER, f_signals INTEGER,
  interest_coverage REAL, current_ratio REAL, debt_equity REAL, bs_score REAL,
  distress TEXT, updated_date TEXT);
"""


def test_load_periods_picks_consolidated_and_prior_year():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_EF)
    # consolidated should win over standalone; prior must be ~1yr before now
    conn.executemany(
        "INSERT INTO extracted_financials (symbol, period_ending, scope, pat_cr, total_assets_cr) "
        "VALUES (?,?,?,?,?)",
        [("X", "2026-03-31", "consolidated", 120, 1000),
         ("X", "2025-03-31", "consolidated", 80, 950),
         ("X", "2026-03-31", "standalone", 110, 900)])
    conn.commit()
    now, prior = ss.load_periods(conn, "X")
    assert now["pat_cr"] == 120 and now["total_assets_cr"] == 1000   # consolidated
    assert prior["pat_cr"] == 80                                     # the year-ago period


def test_run_strength_pass_persists():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_EF)
    conn.executemany(
        "INSERT INTO extracted_financials (symbol, period_ending, scope, pat_cr, pbt_cr, "
        "finance_cost_cr, cfo_cr, current_assets_cr, current_liabilities_cr, borrowings_cr, "
        "equity_cr) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [("STRONG", "2026-03-31", "standalone", 100, 110, 5, 120, 300, 100, 20, 500)])
    conn.commit()
    rep = ss.run_strength_pass(conn, ["STRONG", "MISSING"])
    assert rep["symbols"] == 2 and rep["scored"] == 1            # MISSING has no financials
    row = conn.execute(
        "SELECT bs_score, interest_coverage, distress FROM stock_strength WHERE symbol='STRONG'"
    ).fetchone()
    assert row[0] is not None and row[1] == 23.0 and row[2] is None   # (110+5)/5; no distress
