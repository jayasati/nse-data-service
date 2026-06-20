"""Tests for the paper_book expectancy report (plan R1).

Pure metric maths pinned with hand-computed numbers; report() exercised against an
in-memory paper_book so the SQL grouping + empty-case are covered.
"""
from __future__ import annotations

import sqlite3

from nse_data.research import paper_report as pr


def test_trade_metrics_basic():
    m = pr.trade_metrics([10.0, -5.0, 20.0, -10.0])
    assert m["n"] == 4 and m["n_win"] == 2 and m["n_loss"] == 2
    assert m["win_rate"] == 0.5
    assert m["avg_win"] == 15.0          # (10+20)/2
    assert m["avg_loss"] == -7.5         # (-5-10)/2
    assert m["payoff_ratio"] == 2.0      # 15 / 7.5
    assert m["profit_factor"] == 2.0     # 30 / 15
    assert m["expectancy"] == 3.75       # 15/4
    assert m["total"] == 15.0
    assert m["best"] == 20.0 and m["worst"] == -10.0


def test_trade_metrics_low_winrate_can_be_positive():
    # 1 win in 4 (25% win rate) but a fat winner → POSITIVE expectancy
    m = pr.trade_metrics([30.0, -3.0, -3.0, -3.0])
    assert m["win_rate"] == 0.25
    assert m["expectancy"] > 0           # 21/4 = 5.25 — the whole point of the reframe
    assert m["profit_factor"] > 1


def test_trade_metrics_no_losses_ratios_none():
    m = pr.trade_metrics([5.0, 10.0])
    assert m["profit_factor"] is None    # undefined (no losses) → formatter shows ∞
    assert m["payoff_ratio"] is None


def test_trade_metrics_empty():
    m = pr.trade_metrics([])
    assert m["n"] == 0 and m["expectancy"] is None and m["total"] == 0.0


def test_max_drawdown():
    # +10% then -50% off the peak: equity 1.1 → 0.55, DD = 50%
    assert round(pr.max_drawdown([10.0, -50.0]), 2) == 50.0
    assert pr.max_drawdown([5.0, 5.0]) == 0.0      # monotonic up → no drawdown


_DDL = ("CREATE TABLE paper_book (id INTEGER PRIMARY KEY, symbol TEXT, strategy TEXT, "
        "status TEXT, net_pct REAL, exit_reason TEXT, entry_date TEXT, exit_date TEXT, "
        "r_multiple REAL);")


def _book(rows):
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    conn.executemany(
        "INSERT INTO paper_book (symbol, strategy, status, net_pct, exit_reason, "
        "entry_date, exit_date, r_multiple) VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


def test_report_groups_by_strategy_and_handles_open():
    conn = _book([
        ("A", "lean", "closed", 12.0, "t_out", "2026-01-01", "2026-01-10", 2.0),
        ("B", "lean", "closed", -4.0, "stop", "2026-01-02", "2026-01-05", -1.0),
        ("C", "lean", "open", None, None, "2026-01-09", None, None),
        ("D", "qvm", "closed", 3.0, "trail", "2026-01-01", "2026-01-20", 0.5),
    ])
    rep = pr.report(conn)
    assert set(rep["strategies"]) == {"lean", "qvm"}
    lean = rep["strategies"]["lean"]
    assert lean["n"] == 2 and lean["win_rate"] == 0.5
    assert lean["expectancy"] == 4.0                 # (12-4)/2
    assert lean["avg_r"] == 0.5                       # (2.0 + -1.0)/2 — the R yardstick
    assert lean["n_with_r"] == 2
    assert rep["open"]["lean"] == 1                   # the open C
    assert "stop" in lean["by_reason"] and "t_out" in lean["by_reason"]
    assert lean["avg_hold_days"] == 6.0              # (9 + 3) / 2
    assert lean["validation"]["verdict"] == "insufficient"   # R9: only 2 trades
    assert lean["validation"]["n"] == 2


def test_health_scorecard_and_kill_list():
    # a clearly losing strategy with a meaningful sample → red + on the kill list
    rows = [("L%d" % i, "qvm", "closed", -2.0 if i % 3 else 1.0, "stop", "2026-01-01",
             "2026-01-05", -0.5) for i in range(60)]                       # ~33% win, PF < 1
    conn = _book(rows)
    rep = pr.report(conn)
    qvm = rep["strategies"]["qvm"]
    assert qvm["health"] == "red" and "qvm" in rep["kill_list"]
    # too few trades → insufficient, never on the kill list
    rep2 = pr.report(_book([("A", "lean", "closed", 5.0, "t_out", "2026-01-01", "2026-01-03", 1.0)]))
    assert rep2["strategies"]["lean"]["health"] == "insufficient" and rep2["kill_list"] == []


def test_report_empty_book():
    conn = sqlite3.connect(":memory:")
    conn.executescript(_DDL)
    rep = pr.report(conn)
    assert rep["strategies"] == {}
    assert "no closed trades yet" in pr.format_report(rep)
