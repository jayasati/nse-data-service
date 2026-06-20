"""Tests for the paper-book monitor dashboard."""
from __future__ import annotations

import sqlite3

from nse_data.research import paper_monitor as pm

_BOOK = """
CREATE TABLE paper_book (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, strategy TEXT,
  status TEXT, entry_date TEXT, entry_px REAL, last_score REAL, stop_px REAL, trail_stop REAL,
  qty INTEGER, risk_rupees REAL, net_pct REAL, r_multiple REAL, exit_reason TEXT, exit_date TEXT);
CREATE TABLE raw_intraday_candles (symbol TEXT, interval TEXT, ts INTEGER, close REAL);
"""

_TS = 1_780_000_000        # a fixed epoch → some IST date


def _conn(rows_pb=(), prices=()):
    c = sqlite3.connect(":memory:")
    c.executescript(_BOOK)
    c.executemany("INSERT INTO paper_book (symbol,strategy,status,entry_date,entry_px,last_score,"
                  "stop_px,trail_stop,qty,risk_rupees,net_pct,r_multiple,exit_reason,exit_date) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_pb)
    c.executemany("INSERT INTO raw_intraday_candles VALUES (?,?,?,?)",
                  [(s, "day", _TS, px) for s, px in prices])
    c.commit()
    return c


def test_empty_book():
    c = sqlite3.connect(":memory:")
    snap = pm.monitor_snapshot(c)
    assert snap["strategies"] == {}
    assert "no positions yet" in pm.format_monitor(snap)


def test_open_positions_no_closed():
    c = _conn(
        rows_pb=[
            ("AAA", "lean", "open", "2026-06-18", 100.0, 86, 95.0, None, 100, 1000, None, None, None, None),
            ("BBB", "lean", "open", "2026-06-19", 200.0, 81, 190.0, None, 50, 1000, None, None, None, None),
        ],
        prices=[("AAA", 110.0), ("BBB", 190.0)])
    snap = pm.monitor_snapshot(c)
    lean = snap["strategies"]["lean"]
    assert lean["n_open"] == 2 and lean["progress"]["closed"] == 0
    aaa = next(o for o in lean["open"] if o["symbol"] == "AAA")
    assert aaa["unrealized_pct"] == 10.0 and aaa["open_r"] == 2.0      # +10% = +2R on a 5-wide stop
    out = pm.format_monitor(snap)
    assert "holdings:" in out and "no closed trades yet" in out and "AAA" in out


def test_with_closed_shows_expectancy_and_verdict():
    rows = [("C%d" % i, "lean", "closed", "2026-01-01", 100.0, 70, 95.0, None, 100, 1000,
             (6.0 if i % 2 == 0 else -3.0), (1.2 if i % 2 == 0 else -1.0),
             "t_out", "2026-01-10") for i in range(40)]
    c = _conn(rows_pb=rows, prices=[("C0", 100.0)])
    snap = pm.monitor_snapshot(c)
    lean = snap["strategies"]["lean"]
    assert lean["progress"]["closed"] == 40 and lean["progress"]["pct"] == 40   # 40/100
    assert lean["closed"]["expectancy"] == 1.5                                   # (6-3)/2
    assert "validation" in lean["closed"]
    out = pm.format_monitor(snap)
    assert "Expectancy" in out and "Verdict:" in out and "to significance" in out
