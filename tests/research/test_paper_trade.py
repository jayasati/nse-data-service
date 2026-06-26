"""State-machine + risk-sizing tests for the paper-trade engine core.

`_run_strategy` is exercised against an in-memory paper_book (migration 080/084/089
schema) so the BUY/SELL/peak/exit-reason transitions, the R4 ATR sizing, and the
R2 delivery-cost net P&L are pinned — independent of the live engines/universe.
"""
from __future__ import annotations

import sqlite3

from nse_data.research import paper_trade as pt

_BOOK = """
CREATE TABLE paper_book (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL, entry_date TEXT NOT NULL, entry_px REAL,
  entry_score REAL, peak_score REAL, last_score REAL,
  status TEXT NOT NULL DEFAULT 'open',
  exit_date TEXT, exit_px REAL, exit_reason TEXT, net_pct REAL,
  updated_at INTEGER, strategy TEXT NOT NULL DEFAULT 'qvm',
  stop_px REAL, qty INTEGER, risk_rupees REAL, net_pnl REAL, r_multiple REAL, trail_stop REAL,
  direction TEXT DEFAULT 'long'
);
"""


def _conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_BOOK)
    return c


def _run(conn, score, today, prices, atr=None, sectors=None, chand=None, **overrides):
    params = pt.PaperTradeParams(**overrides)
    return pt._run_strategy(conn, "lean", "Lean", score, today, params,
                            price_now=lambda s: prices.get(s),
                            risk_tag=lambda s: "", atr_of=lambda s: atr,
                            sector_of=lambda s: (sectors or {}).get(s, "x"),
                            chand_of=lambda s, d="long": chand)


def test_buy_opens_and_sizes():
    conn = _conn()
    summ = _run(conn, {"AAA": 85.0, "BBB": 50.0}, "2026-06-20", {"AAA": 100.0, "BBB": 200.0})
    assert ("AAA", 85.0) in summ["buys"]
    assert all(s != "BBB" for s, _ in summ["buys"])      # 50 < t_in 80
    # default atr=None → flat 10% stop; capital 1M, risk 1% → 10k risk / ₹10 per-share
    row = conn.execute(
        "SELECT status, entry_px, stop_px, qty, risk_rupees FROM paper_book "
        "WHERE symbol='AAA'").fetchone()
    assert row == ("open", 100.0, 90.0, 1000, 10000.0)
    assert summ["n_open"] == 1


def test_atr_sets_stop_and_qty():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0}, atr=2.0)   # k=2.5 → 5% stop
    stop, qty, risk = conn.execute(
        "SELECT stop_px, qty, risk_rupees FROM paper_book WHERE symbol='AAA'").fetchone()
    assert stop == 95.0 and qty == 2000 and risk == 10000.0  # 10k risk / ₹5 per-share


def test_no_double_open_when_already_held():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0})
    summ = _run(conn, {"AAA": 88.0}, "2026-06-21", {"AAA": 105.0})       # re-run, still held
    assert summ["buys"] == []
    assert conn.execute("SELECT COUNT(*) FROM paper_book WHERE symbol='AAA'").fetchone()[0] == 1


def test_t_out_closes_net_of_cost_and_r():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0}, atr=2.0)
    summ = _run(conn, {"AAA": 50.0}, "2026-06-21", {"AAA": 110.0}, atr=2.0)   # +10% gross
    net = next(net for s, net, r in summ["sells"] if s == "AAA" and r == "t_out")
    assert 9.0 < net < 10.0                              # +10% gross − small delivery cost
    status, npct, npnl, r = conn.execute(
        "SELECT status, net_pct, net_pnl, r_multiple FROM paper_book WHERE symbol='AAA'").fetchone()
    assert status == "closed" and 9.0 < npct < 10.0
    assert npnl > 0 and 1.5 < r < 2.1                    # +10% on a 5% stop ≈ +2R (minus cost)


def test_atr_stop_fires_before_catastrophe_stop():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0}, atr=2.0)    # stop_px 95
    # price 94 is only −6% (catastrophe −15% NOT hit) but breaches the ATR stop
    summ = _run(conn, {"AAA": 85.0}, "2026-06-21", {"AAA": 94.0}, atr=2.0)
    assert any(s == "AAA" and r == "stop" for s, _net, r in summ["sells"])
    r = conn.execute("SELECT r_multiple FROM paper_book WHERE symbol='AAA'").fetchone()[0]
    assert r < 0                                          # a loss in R


def test_chandelier_ratchets_up_and_locks_gain():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0}, atr=2.0)        # stop_px 95
    # price runs to 130; chandelier 122 ratchets the trailing stop up (kept ≤ price)
    _run(conn, {"AAA": 85.0}, "2026-06-21", {"AAA": 130.0}, atr=2.0, chand=122.0)
    assert conn.execute("SELECT trail_stop FROM paper_book WHERE symbol='AAA'").fetchone()[0] == 122.0
    # price falls to 121 (< trailing 122, > initial stop 95) → chandelier exit, gain locked
    summ = _run(conn, {"AAA": 85.0}, "2026-06-22", {"AAA": 121.0}, atr=2.0, chand=120.0)
    assert any(s == "AAA" and r == "chandelier" for s, _net, r in summ["sells"])
    npct, r = conn.execute(
        "SELECT net_pct, r_multiple FROM paper_book WHERE symbol='AAA'").fetchone()
    assert npct > 0 and r > 0                                # +21% gross locked, positive R


def test_chandelier_never_moves_down():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0}, atr=2.0)
    _run(conn, {"AAA": 85.0}, "2026-06-21", {"AAA": 130.0}, atr=2.0, chand=122.0)  # trail → 122
    # a later, lower chandelier must NOT lower the stored trail_stop
    _run(conn, {"AAA": 85.0}, "2026-06-22", {"AAA": 128.0}, atr=2.0, chand=110.0)
    assert conn.execute("SELECT trail_stop FROM paper_book WHERE symbol='AAA'").fetchone()[0] == 122.0


def test_initial_stop_takes_precedence_over_chandelier():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0}, atr=2.0)        # stop_px 95
    # deep drop below the initial stop → 'stop' (the R floor), not 'chandelier'
    summ = _run(conn, {"AAA": 85.0}, "2026-06-21", {"AAA": 90.0}, atr=2.0, chand=93.0)
    assert any(s == "AAA" and r == "stop" for s, _net, r in summ["sells"])


def test_trail_closes_off_peak():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0})
    _run(conn, {"AAA": 95.0}, "2026-06-21", {"AAA": 100.0})              # peak rises to 95
    summ = _run(conn, {"AAA": 78.0}, "2026-06-22", {"AAA": 100.0})       # 95−78=17 >= trail 15
    assert any(s == "AAA" and r == "trail" for s, _net, r in summ["sells"])


def test_catastrophe_stop_on_big_drawdown():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0})
    summ = _run(conn, {"AAA": 84.0}, "2026-06-21", {"AAA": 80.0})        # −20% ≤ stop −15
    assert any(s == "AAA" and r == "stop" for s, _net, r in summ["sells"])


def test_dropped_when_score_missing():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0})
    summ = _run(conn, {}, "2026-06-21", {"AAA": 100.0})                  # no longer scored/eligible
    assert any(s == "AAA" and r == "dropped" for s, _net, r in summ["sells"])


def test_max_hold_forces_exit():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-01", {"AAA": 100.0})
    # 121 days later, score still healthy and price flat → only max_hold can close it
    summ = _run(conn, {"AAA": 90.0}, "2026-09-30", {"AAA": 100.0})
    assert any(s == "AAA" and r == "max_hold" for s, _net, r in summ["sells"])


def test_legacy_row_without_qty_uses_flat_cost():
    conn = _conn()
    # simulate a position opened before sizing existed (NULL stop/qty/risk)
    conn.execute("INSERT INTO paper_book (symbol, entry_date, entry_px, entry_score, "
                 "peak_score, last_score, status, strategy) "
                 "VALUES ('OLD','2026-06-20',100,85,85,85,'open','lean')")
    conn.commit()
    summ = _run(conn, {"OLD": 50.0}, "2026-06-21", {"OLD": 110.0})       # t_out, +10% gross
    net = next(net for s, net, r in summ["sells"] if s == "OLD")
    assert net == 9.0                                    # flat 1.0 fallback: 10 − 1
    npnl, r = conn.execute(
        "SELECT net_pnl, r_multiple FROM paper_book WHERE symbol='OLD'").fetchone()
    assert npnl is None and r is None                    # no sizing → no rupee P&L / R


def test_dry_run_writes_nothing():
    conn = _conn()
    summ = _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0}, dry_run=True)
    assert ("AAA", 85.0) in summ["buys"]
    assert conn.execute("SELECT COUNT(*) FROM paper_book").fetchone()[0] == 0


def test_lean_runs_in_coverage_mode():
    p = pt._effective_params("lean", pt.PaperTradeParams())
    assert p.max_positions == 75 and p.heat_pct == 100.0 and p.sector_max == 25   # broad coverage


def test_other_strategies_keep_portfolio_caps():
    p = pt._effective_params("qvm", pt.PaperTradeParams())
    assert p.max_positions == 10 and p.heat_pct == 10.0 and p.sector_max == 3      # R5 defaults


def test_explicit_flag_overrides_coverage_default():
    # a caller who sets max_positions explicitly wins; the other coverage caps still apply
    p = pt._effective_params("lean", pt.PaperTradeParams(max_positions=5))
    assert p.max_positions == 5 and p.heat_pct == 100.0 and p.sector_max == 25


def test_risk_kill_switch_blocks_new_buys():
    conn = _conn()
    # book down ₹30k today (> 2% of ₹1M capital) → kill switch
    conn.execute("INSERT INTO paper_book (symbol,strategy,status,entry_date,exit_date,net_pnl,net_pct) "
                 "VALUES ('L','lean','closed','2026-06-01','2026-06-20',-30000,-30)")
    conn.commit()
    summ = _run(conn, {"AAA": 90.0}, "2026-06-20", {"AAA": 100.0})
    assert summ["kill_switch"] is True and summ["buys"] == []


def test_risk_consecutive_losses_halve_size():
    conn = _conn()
    for i, d in enumerate(["2026-06-17", "2026-06-18", "2026-06-19"]):     # 3 losses in a row
        conn.execute("INSERT INTO paper_book (symbol,strategy,status,entry_date,exit_date,net_pct,net_pnl) "
                     "VALUES (?, 'lean','closed','2026-06-01',?,-2,-100)", (f"X{i}", d))
    conn.commit()
    summ = _run(conn, {"AAA": 90.0}, "2026-06-20", {"AAA": 100.0}, atr=2.0)
    assert summ["kill_switch"] is False and summ["size_mult"] == 0.5
    assert conn.execute("SELECT qty FROM paper_book WHERE symbol='AAA'").fetchone()[0] == 1000  # half of 2000


def test_risk_vix_and_toggle():
    conn = _conn()
    p = pt.PaperTradeParams()
    assert pt._risk_state(conn, "lean", "2026-06-20", p, 25.0) == (False, 0.6)   # VIX>22 → 0.6
    assert pt._risk_state(conn, "lean", "2026-06-20", p, 15.0) == (False, 1.0)
    off = pt.PaperTradeParams(risk_rules=False)
    assert pt._risk_state(conn, "lean", "2026-06-20", off, 99.0) == (False, 1.0)  # disabled


def test_max_positions_cap_prefers_top_scores():
    conn = _conn()
    score = {f"S{i}": 90.0 - i for i in range(5)}        # S0=90 … S4=86, all ≥ t_in
    summ = _run(conn, score, "2026-06-20", {k: 100.0 for k in score}, max_positions=2)
    assert [s for s, _ in summ["buys"]] == ["S0", "S1"]  # the two best, capped at 2
    assert summ["eligible_buys"] == 5 and summ["capped"] == 3


def test_sector_concentration_cap():
    conn = _conn()
    score = {"A": 90.0, "B": 89.0, "C": 88.0, "D": 87.0}
    sectors = {"A": "bank", "B": "bank", "C": "bank", "D": "it"}
    summ = _run(conn, score, "2026-06-20", {k: 100.0 for k in score},
                sectors=sectors, sector_max=2)
    assert [s for s, _ in summ["buys"]] == ["A", "B", "D"]  # 3rd bank (C) skipped, IT opens
    assert summ["capped"] == 1


def test_portfolio_heat_cap():
    conn = _conn()
    score = {"A": 90.0, "B": 89.0, "C": 88.0}
    # heat budget 1.5% of 1M = ₹15k; each trade risks ₹10k (1%) → only the first fits
    summ = _run(conn, score, "2026-06-20", {k: 100.0 for k in score}, heat_pct=1.5)
    assert len(summ["buys"]) == 1 and summ["capped"] == 2
    assert summ["heat_used_pct"] == 1.0                  # one 1% position


def test_cap_telemetry_when_uncapped():
    conn = _conn()
    summ = _run(conn, {"A": 90.0}, "2026-06-20", {"A": 100.0})
    assert summ["eligible_buys"] == 1 and summ["capped"] == 0
    assert summ["heat_used_pct"] == 1.0


def test_closed_summary_stats():
    conn = _conn()
    _run(conn, {"AAA": 85.0}, "2026-06-20", {"AAA": 100.0}, atr=2.0)
    summ = _run(conn, {"AAA": 50.0}, "2026-06-21", {"AAA": 110.0}, atr=2.0)   # one closed winner
    assert summ["closed_n"] == 1
    assert summ["win_pct"] == 100.0
    assert 9.0 < summ["avg_pct"] < 10.0
