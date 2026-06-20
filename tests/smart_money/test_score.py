"""Tests for the smart-money composite score."""
from __future__ import annotations

import sqlite3

from nse_data.smart_money import score as sm


def test_flow_score():
    assert sm.flow_score(600) == 0.7 and sm.flow_score(-600) == 0.3
    assert sm.flow_score(100) == 0.5 and sm.flow_score(None) is None


def test_fii_deriv_score():
    bull = sm.fii_deriv_score({"fut_idx_long": 100, "fut_idx_short": 50,
                               "opt_idx_call_long": 200, "opt_idx_put_long": 100})
    bear = sm.fii_deriv_score({"fut_idx_long": 50, "fut_idx_short": 100,
                               "opt_idx_call_long": 100, "opt_idx_put_long": 200})
    assert bull == 0.7 and bear == 0.3
    assert sm.fii_deriv_score(None) is None


def test_block_tier_score():
    assert sm.block_tier_score(50) == 0.75 and sm.block_tier_score(-50) == 0.35
    assert sm.block_tier_score(None) is None


def test_composite_weighted_and_graceful():
    full = sm.composite({"fii_cash": 0.7, "fii_deriv": 0.7, "dii": 0.65, "block_tier": 0.75})
    assert abs(full - 0.7) < 1e-9                          # weighted mean
    assert sm.composite({"fii_cash": 0.7}) == 0.7          # single component re-normalises
    assert sm.composite({}) is None


def test_run_pass_persists_score():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE raw_fii_dii (date TEXT, category TEXT, net_value REAL, fetched_at INTEGER);"
        "CREATE TABLE raw_participant_oi (client_type TEXT, report_date TEXT, fut_idx_long INT, "
        "fut_idx_short INT, opt_idx_call_long INT, opt_idx_put_long INT);"
        "CREATE TABLE raw_large_deals (deal_type TEXT, symbol TEXT, client_name TEXT, "
        "buy_sell TEXT, quantity INT, weighted_avg_price REAL);"
        "CREATE TABLE smart_money_daily (as_of_date TEXT PRIMARY KEY, score REAL, fii_cash REAL, "
        "fii_deriv REAL, dii REAL, block_tier REAL, components TEXT, updated_at INTEGER);")
    conn.execute("INSERT INTO raw_fii_dii VALUES ('27-May-2026','FII/FPI', 1200, 1)")
    conn.execute("INSERT INTO raw_fii_dii VALUES ('27-May-2026','DII', 800, 1)")
    conn.execute("INSERT INTO raw_participant_oi VALUES ('FII','2026-05-27',100,50,200,100)")
    conn.commit()
    rep = sm.run_smart_money_pass(conn, now="2026-05-27")
    assert rep["score"] is not None and "fii_cash" in rep["components"]
    row = conn.execute("SELECT score, fii_deriv FROM smart_money_daily WHERE as_of_date='2026-05-27'").fetchone()
    assert row[0] is not None and row[1] == 0.7            # FII bullish derivatives
