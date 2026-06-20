"""Tests for W24: institution tiering + short-squeeze detector."""
from __future__ import annotations

import sqlite3

from nse_data.research import ownership_flow as of
from nse_data.smart_money import short_squeeze as sq
from nse_data.smart_money import tiers


# ---- 24.1 institution tiering ---------------------------------------------

def test_tier_of_classifies_known_institutions():
    assert tiers.tier_of("LIC of India") == "tier1"          # substring, case-insensitive
    assert tiers.tier_of("Motilal Oswal Financial") == "tier2"
    assert tiers.tier_of("Some Retail Person") is None
    assert tiers.tier_of(None) is None


def test_block_flow_breaks_out_tier1_net():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE raw_large_deals (deal_type TEXT, deal_date TEXT, symbol TEXT, "
                 "client_name TEXT, buy_sell TEXT, quantity INTEGER, weighted_avg_price REAL)")
    conn.executemany("INSERT INTO raw_large_deals VALUES (?,?,?,?,?,?,?)", [
        ("block", "26-May-2026", "X", "LIC Mutual Fund", "BUY", 500_000, 200.0),   # tier1 +10cr
        ("block", "26-May-2026", "X", "Retail Trader", "SELL", 100_000, 200.0)])   # retail
    conn.commit()
    f = of.block_flow(conn, "X", days=90)
    assert f["tier1_net_cr"] == 10.0 and f["tier2_net_cr"] == 0.0
    assert f["net_value_cr"] == 8.0                          # 10 buy − 2 sell overall


# ---- 24.2 short-squeeze ----------------------------------------------------

def test_is_short_squeeze_core_and_slb_optional():
    assert sq.is_short_squeeze(-25.0, True, True) is True            # deep fall + delivery + recovering
    assert sq.is_short_squeeze(-10.0, True, True) is False           # not deep enough
    assert sq.is_short_squeeze(-25.0, False, True) is False          # delivery not rising
    assert sq.is_short_squeeze(-25.0, True, False) is False          # not recovering
    assert sq.is_short_squeeze(-25.0, True, True, slb_falling=True) is True   # SLB confirms
    assert sq.is_short_squeeze(-25.0, True, True, slb_falling=False) is False  # SLB contradicts


def test_run_short_squeeze_pass_finds_candidate():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE raw_intraday_candles (symbol TEXT, interval TEXT, ts INTEGER, close REAL);"
        "CREATE TABLE delivery_conviction (symbol TEXT, session_date TEXT, delivery_trend TEXT);")
    # SQUEEZE: 16 daily closes (oldest→newest) falling 100→72 then ticking up; delivery rising
    closes = [100 - i * 2 for i in range(16)]               # 100,98,…,70 — deep fall
    closes[-1] = closes[-2] + 1                              # last bar up → recovering (73)
    conn.executemany("INSERT INTO raw_intraday_candles VALUES ('SQ','day',?,?)",
                     [(1000 + i, c) for i, c in enumerate(closes)])
    conn.execute("INSERT INTO delivery_conviction VALUES ('SQ','2026-06-19','rising')")
    conn.commit()
    rep = sq.run_short_squeeze_pass(conn, symbols=["SQ"])
    assert rep["squeezes"] == 1 and rep["hits"][0]["symbol"] == "SQ"
