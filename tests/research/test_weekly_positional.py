"""Weekly positional — slow-data overlay + basket selection/sizing."""
from __future__ import annotations

import sqlite3

from nse_data.research.paper_trade import PaperTradeParams
from nse_data.research.weekly_positional import _overlay, rank_week


def _row(sym="X", lean=80.0, sector="it"):
    return {"symbol": sym, "sector": sector, "grade": "A core", "lean_score": lean,
            "valuation": 70, "surprise": 90, "risk": 100}


def test_overlay_boosts_and_penalties():
    # FII adding + pledge falling + high delivery → score lifts, no exclude
    adj, flags, excl = _overlay(_row(lean=80), {"fii_delta": 1.0, "pledge_delta": -2.0},
                                {"conviction": 0.7}, None, 8.0)
    assert not excl and adj > 80 and "FII↑" in flags and "pledge↓" in flags and "block-buy" in flags
    # rising pledge + FII exiting → score drops
    adj2, flags2, _ = _overlay(_row(lean=80), {"fii_delta": -1.0, "pledge_delta": 3.0}, None, None, None)
    assert adj2 < 80 and "pledge↑" in flags2 and "FII↓" in flags2


def test_overlay_hard_excludes():
    _, f1, e1 = _overlay(_row(), {}, None, {"action": "downgrade", "junk": True}, None)
    assert e1 and "junk-downgrade" in f1                                # junk downgrade
    _, f2, e2 = _overlay(_row(), {"pledge_pct": 60, "pledge_delta": 3}, None, None, None)
    assert e2 and "pledge-high↑" in f2                                  # high & rising pledge


def test_rank_week_sizes_and_sector_caps():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(open("migrations/097_weekly_ranking.sql").read())
    # minimal stubs so lean_rank + sizing have data: factor_snapshot + tradeable_universe + candles
    conn.executescript(
        "CREATE TABLE factor_snapshot(snapshot_date TEXT, symbol TEXT, sector TEXT, valuation REAL,"
        " surprise REAL, risk REAL);"
        "CREATE TABLE tradeable_universe(symbol TEXT, grade TEXT, med_turnover_cr REAL,"
        " market_cap_cr REAL, atr_pct REAL);"
        "CREATE TABLE raw_intraday_candles(symbol TEXT, ts INT, interval TEXT, close REAL);")
    for i, s in enumerate(["AA", "BB", "CC", "DD"]):           # all 'it' sector → cap should bite
        conn.execute("INSERT INTO factor_snapshot VALUES('2026-06-19',?, 'it', ?, ?, 100)",
                     (s, 90 - i, 90 - i))
        conn.execute("INSERT INTO tradeable_universe VALUES(?, 'A core', 50, 5000, 2.0)", (s,))
        conn.execute("INSERT INTO raw_intraday_candles VALUES(?, 1, 'day', 100)", (s,))
    conn.commit()
    ranked = rank_week(conn, params=PaperTradeParams(sector_max=2), top_n=10, session_date=None)
    sized = [r for r in ranked if r["selected"]]
    assert len(sized) == 2                                      # sector_max=2 caps the 'it' basket
    assert all(r["qty"] and r["risk_rupees"] for r in sized)    # every pick is ATR-sized
