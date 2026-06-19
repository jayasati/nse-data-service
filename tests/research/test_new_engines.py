"""Unit tests for the Liquidity (P6) and Surprise (P5) ranking engines.

In-memory SQLite with the minimal columns each engine reads. Pins the behaviours
the backtest rig depends on: point-in-time windows, the recency event-gate, the
consensus-then-proxy expectation, and score bounds.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

from nse_data.research import liquidity_engine as liq
from nse_data.research import surprise_engine as sur

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _ep(datestr: str) -> int:
    return int(_dt.datetime.strptime(datestr, "%Y-%m-%d").replace(tzinfo=_IST).timestamp())


def _one_sector(_s):
    return "generic"


# ---------------- Liquidity ----------------

def _candle_db(rows):
    """rows: (symbol, date 'YYYY-MM-DD', close, volume)."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE raw_intraday_candles(symbol TEXT, interval TEXT, ts INT, "
              "close REAL, volume REAL)")
    c.executemany("INSERT INTO raw_intraday_candles VALUES (?,'day',?,?,?)",
                  [(s, _ep(d), cl, v) for s, d, cl, v in rows])
    return c


def test_liquidity_ranks_higher_turnover_above_thin():
    # 40 sessions each; BIG trades 100x the value of SMALL at the same price.
    days = [f"2025-01-{d:02d}" for d in range(1, 29)] + [f"2025-02-{d:02d}" for d in range(1, 13)]
    rows = []
    for d in days:
        rows.append(("BIG", d, 100.0, 1_000_000))
        rows.append(("SMALL", d, 100.0, 10_000))
    conn = _candle_db(rows)
    out = liq.score_universe(conn, ["BIG", "SMALL"], _ep("2025-02-15"), _one_sector)
    assert out["BIG"]["score"] > out["SMALL"]["score"]
    for r in out.values():
        assert 0.0 <= r["score"] <= 100.0


def test_liquidity_needs_a_real_window():
    conn = _candle_db([("THIN", "2025-01-01", 100.0, 5000)] * 1)
    assert liq.liquidity_raw(conn, "THIN", _ep("2025-02-15")) is None


def test_liquidity_is_point_in_time():
    # volume only exists AFTER the as_of date -> nothing to measure as-of.
    rows = [("X", f"2025-03-{d:02d}", 100.0, 500_000) for d in range(1, 28)]
    conn = _candle_db(rows)
    assert liq.liquidity_raw(conn, "X", _ep("2025-01-15")) is None


# ---------------- Surprise ----------------

def _fin_db(fin_rows, cons_rows=()):
    """fin_rows: (symbol, period_ending, rev, pat, eps, broadcast 'YYYY-MM-DD')."""
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE extracted_financials(symbol TEXT, period_ending TEXT, scope TEXT, "
              "revenue_cr REAL, pat_cr REAL, eps_basic REAL, broadcast_dt TEXT)")
    c.executemany("INSERT INTO extracted_financials VALUES (?,?,'consolidated',?,?,?,?)",
                  [(s, pe, rev, pat, eps, bdt) for s, pe, rev, pat, eps, bdt in fin_rows])
    c.execute("CREATE TABLE consensus_estimates(symbol TEXT, period_ending TEXT, rev_est_cr REAL, "
              "pat_est_cr REAL, eps_est REAL, as_of TEXT)")
    c.executemany("INSERT INTO consensus_estimates VALUES (?,?,?,?,?,?)", list(cons_rows))
    return c


def _growing_quarters(symbol, latest_pat, base_pat=100.0, g=0.2):
    """6 quarterly prints, ~g YoY growth, the latest one overridden to latest_pat."""
    pes = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30"]
    rows = []
    for i, pe in enumerate(pes):
        pat = base_pat * (1 + g) ** (i / 4.0)
        bdt = (_dt.date.fromisoformat(pe) + _dt.timedelta(days=20)).isoformat()
        rows.append((symbol, pe, pat * 10, pat, pat / 10, bdt))
    s, pe, rev, _, eps, bdt = rows[-1]
    rows[-1] = (s, pe, latest_pat * 10, latest_pat, latest_pat / 10, bdt)
    return rows


def test_surprise_proxy_beat_scores_above_miss():
    # BEAT prints well above its trend; MISS prints below. Same sector pool.
    rows = _growing_quarters("BEAT", latest_pat=200.0) + _growing_quarters("MISS", latest_pat=110.0)
    conn = _fin_db(rows)
    asof = _ep("2025-07-25")            # within 75d of the 2025-07-20 broadcast
    out = sur.score_universe(conn, ["BEAT", "MISS"], asof, _one_sector)
    assert out["BEAT"]["score"] > out["MISS"]["score"]


def test_surprise_recency_gate_drops_stale_results():
    rows = _growing_quarters("OLD", latest_pat=200.0)
    conn = _fin_db(rows)
    # as_of is >75d after the latest broadcast (2025-07-20) -> no fresh event.
    assert sur.surprise_raw(conn, "OLD", _ep("2025-12-31")) is None


def test_surprise_uses_consensus_when_it_predates_the_print():
    rows = _growing_quarters("CONS", latest_pat=150.0)
    # consensus expected pat far BELOW the 150 actual, estimated before the print
    cons = [("CONS", "2025-06-30", 1000.0, 90.0, 9.0, "2025-06-01")]
    conn = _fin_db(rows, cons)
    f = sur.surprise_raw(conn, "CONS", _ep("2025-07-25"))
    assert f is not None and f["surprise_pat"] > 0      # 150 vs 90 consensus = beat


def test_surprise_ignores_consensus_dated_after_the_print():
    rows = _growing_quarters("LATE", latest_pat=150.0)
    # estimate stamped AFTER the result -> must be ignored (would be look-ahead)
    cons = [("LATE", "2025-06-30", 1000.0, 90.0, 9.0, "2025-08-01")]
    conn = _fin_db(rows, cons)
    assert sur._consensus(conn, "LATE", "2025-06-30", _ep("2025-07-20")) is None
