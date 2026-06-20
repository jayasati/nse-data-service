"""Tests for the pre-buy conviction card assembler (R16)."""
from __future__ import annotations

import sqlite3

from nse_data.research import pre_buy_card as pbc


def test_build_card_on_empty_db_degrades():
    conn = sqlite3.connect(":memory:")                  # no tables at all
    card = pbc.build_card(conn, "AAA")
    assert card["symbol"] == "AAA"
    assert card["price"] is None and card["valuation"] is None and card["risk_plan"] is None
    out = pbc.format_card(card)                          # must not crash on an empty card
    assert "AAA" in out and out.startswith("┌")


_DDL = """
CREATE TABLE tradeable_universe (symbol TEXT, grade TEXT, atr_pct REAL);
CREATE TABLE raw_intraday_candles (symbol TEXT, interval TEXT, ts INTEGER, close REAL);
CREATE TABLE stock_fundamentals (symbol TEXT, quality_score REAL, roe REAL, roce REAL,
  pe_ratio REAL, promoter_holding REAL, promoter_pledge REAL);
CREATE TABLE factor_snapshot (symbol TEXT, snapshot_date TEXT, valuation REAL,
  sector_rank INTEGER, sector_n INTEGER, grade TEXT, composite REAL);
CREATE TABLE extracted_financials (symbol TEXT, period_ending TEXT, scope TEXT,
  pat_cr REAL, pbt_cr REAL, finance_cost_cr REAL, cfo_cr REAL, revenue_cr REAL,
  cost_of_materials_cr REAL, total_assets_cr REAL, current_assets_cr REAL,
  current_liabilities_cr REAL, total_liabilities_cr REAL, borrowings_cr REAL,
  equity_cr REAL, eps_basic REAL, broadcast_dt TEXT);
CREATE TABLE delivery_conviction (symbol TEXT, session_date TEXT, delivery_trend TEXT,
  delivery_ratio REAL);
CREATE TABLE paper_book (symbol TEXT, strategy TEXT, status TEXT, net_pct REAL, r_multiple REAL);
"""


def _populated():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    conn.execute("INSERT INTO tradeable_universe VALUES ('Z','A core',2.0)")
    conn.execute("INSERT INTO raw_intraday_candles VALUES ('Z','day',1000,100.0)")
    conn.execute("INSERT INTO stock_fundamentals VALUES ('Z',75,18,22,20,60,30)")  # pledge 30
    conn.execute("INSERT INTO factor_snapshot VALUES ('Z','2026-06-19',80,2,30,'A core',70)")
    conn.execute(
        "INSERT INTO extracted_financials (symbol, period_ending, scope, pat_cr, pbt_cr, "
        "finance_cost_cr, cfo_cr, revenue_cr, cost_of_materials_cr, total_assets_cr, "
        "current_assets_cr, current_liabilities_cr, borrowings_cr, equity_cr, eps_basic) "
        "VALUES ('Z','2026-03-31','standalone',100,110,5,120,800,300,1000,300,100,20,500,10)")
    conn.execute("INSERT INTO delivery_conviction VALUES ('Z','2026-06-19','rising',0.65)")
    conn.executemany("INSERT INTO paper_book VALUES ('Z','lean','closed',?,?)",
                     [(12.0, 2.0), (-4.0, -1.0)])
    conn.commit()
    return conn


def test_build_card_populated_sections():
    card = pbc.build_card(_populated(), "Z")
    assert card["grade"] == "A core" and card["price"] == 100.0
    assert card["risk_plan"]["stop"] == 95.0 and card["risk_plan"]["qty"] == 2000   # 2xATR sizing
    assert card["valuation"]["sector_rank"] == 2
    assert card["promoter"]["pledge"] == 30
    assert card["strength"]["interest_coverage"] == 23.0                            # (110+5)/5
    assert card["cash"]["cfo_to_pat"] == 1.2                                        # 120/100
    pa = card["paper"]
    assert pa["n"] == 2 and pa["expectancy_pct"] == 4.0 and pa["avg_r"] == 0.5


def test_format_card_renders_key_lines():
    out = pbc.format_card(pbc.build_card(_populated(), "Z"))
    for token in ("Z", "RISK PLAN", "PAPER", "VALUATION", "PROMOTER", "✗"):  # ✗ = pledge 30 > 25
        assert token in out


def test_paper_section_empty_book():
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE paper_book (symbol TEXT, strategy TEXT, status TEXT, "
                       "net_pct REAL, r_multiple REAL);")
    assert pbc._paper(conn)["n"] == 0
