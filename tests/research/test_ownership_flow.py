"""Tests for R11 ownership flows (block/bulk + insider)."""
from __future__ import annotations

import sqlite3

from nse_data.research import ownership_flow as of


def _ld(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE raw_large_deals (deal_type TEXT, deal_date TEXT, symbol TEXT, "
                 "client_name TEXT, buy_sell TEXT, quantity INTEGER, weighted_avg_price REAL)")
    conn.executemany("INSERT INTO raw_large_deals VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


def test_block_flow_net_value_and_counts():
    conn = _ld([
        ("block", "26-May-2026", "X", "FundA", "BUY", 500_000, 200.0),   # +10 Cr
        ("bulk", "20-May-2026", "X", "FundB", "SELL", 300_000, 200.0),   # −6 Cr
        ("block", "01-Jan-2025", "X", "Old", "BUY", 999_999, 200.0),     # > 90d before ref → excluded
        ("short", "26-May-2026", "X", None, None, 1000, None),           # short → excluded
    ])
    f = of.block_flow(conn, "X", days=90)
    assert f["buy_deals"] == 1 and f["sell_deals"] == 1
    assert f["net_value_cr"] == 4.0                                      # 10 − 6


def test_block_flow_none_when_no_deals_for_symbol():
    conn = _ld([("block", "26-May-2026", "X", "F", "BUY", 1000, 100.0)])
    assert of.block_flow(conn, "OTHER") is None


def test_nse_date_parsing_and_recency_excludes_old():
    assert of._nse_date("26-May-2026").isoformat() == "2026-05-26"
    assert of._nse_date(None) is None
    # only the recent deal counts; the year-old one is outside the 90d window from the ref
    conn = _ld([
        ("block", "26-May-2026", "X", "F", "BUY", 100_000, 100.0),
        ("block", "26-May-2025", "X", "F", "BUY", 100_000, 100.0),
    ])
    assert of.block_flow(conn, "X", days=90)["buy_deals"] == 1


def test_insider_flow_none_when_empty():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE raw_insider_trading (symbol TEXT, acquirer_category TEXT, "
                 "transaction_type TEXT, value_in_rupees REAL, intimation_date TEXT)")
    assert of.insider_flow(conn, "X") is None              # empty feed (current state)


def test_insider_flow_net_promoter_buying():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE raw_insider_trading (symbol TEXT, acquirer_category TEXT, "
                 "transaction_type TEXT, value_in_rupees REAL, intimation_date TEXT)")
    conn.executemany("INSERT INTO raw_insider_trading VALUES (?,?,?,?,?)", [
        ("X", "Promoters", "Buy", 50_000_000, "2026-05-20"),   # +5 Cr promoter buy
        ("X", "Promoters", "Sell", 10_000_000, "2026-05-10"),  # −1 Cr
    ])
    conn.commit()
    f = of.insider_flow(conn, "X")
    assert f["net_value_cr"] == 4.0 and f["promoter_buying"] is True


def test_ownership_flow_combined_sections():
    conn = _ld([("block", "26-May-2026", "X", "F", "BUY", 500_000, 200.0)])
    out = of.ownership_flow(conn, "X")
    assert out["block"]["buy_deals"] == 1 and out["insider"] is None   # no insider table
