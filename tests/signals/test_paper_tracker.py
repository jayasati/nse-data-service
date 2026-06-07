"""Paper-trade tracker (signals/paper_tracker.py)."""

from __future__ import annotations

from datetime import datetime

import pytest

from nse_data.scheduler.market_hours import IST
from nse_data.signals import paper_tracker as pt

from .conftest import NOW, SESSION_START, seed_quote


def _seed_signal(conn, *, symbol="ACME", price=100.0, atr=2.0,
                 detected_at=None) -> int:
    detected_at = detected_at or NOW.isoformat()
    cur = conn.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at, price) "
        "VALUES (?, 'long_buildup', ?, ?)",
        (symbol, detected_at, price),
    )
    sid = int(cur.lastrowid)
    if atr is not None:
        conn.execute(
            "INSERT INTO signal_features (signal_id, symbol, detected_at, atr_14_daily) "
            "VALUES (?, ?, ?, ?)",
            (sid, symbol, detected_at, atr),
        )
    conn.commit()
    return sid


def _trade(conn, signal_id):
    return conn.execute(
        "SELECT entry_price, sl_price, t1_price, status, exit_price, exit_reason, "
        "gross_pnl, net_pnl FROM paper_trades WHERE signal_id = ?", (signal_id,),
    ).fetchone()


# ----------------------------------------------------------------- opening

def test_opens_trade_with_atr_bracket(signals_db):
    sid = _seed_signal(signals_db, price=100.0, atr=2.0)
    assert pt.open_new_paper_trades(signals_db, now=NOW) == 1

    entry, sl, t1, status, *_ = _trade(signals_db, sid)
    assert (entry, status) == (100.0, "open")
    assert sl == pytest.approx(97.0)    # 100 - 1.5*2
    assert t1 == pytest.approx(103.0)   # 100 + 1.5*2


def test_skips_when_atr_missing(signals_db):
    _seed_signal(signals_db, price=100.0, atr=None)
    assert pt.open_new_paper_trades(signals_db, now=NOW) == 0


def test_open_is_idempotent(signals_db):
    _seed_signal(signals_db, price=100.0, atr=2.0)
    pt.open_new_paper_trades(signals_db, now=NOW)
    pt.open_new_paper_trades(signals_db, now=NOW)
    assert signals_db.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 1


def test_does_not_open_prior_day_signal(signals_db):
    _seed_signal(signals_db, price=100.0, atr=2.0,
                 detected_at="2025-05-30T10:00:00+05:30")   # not NOW's date
    assert pt.open_new_paper_trades(signals_db, now=NOW) == 0


# ----------------------------------------------------------------- closing

def test_hits_t1(signals_db):
    sid = _seed_signal(signals_db, price=100.0, atr=2.0)
    pt.open_new_paper_trades(signals_db, now=NOW)
    seed_quote(signals_db, "ACME", last_price=103.5, pct_change=3.5, as_of=SESSION_START + 600)

    counts = pt.manage_open_trades(signals_db, now=NOW)
    assert counts["hit_t1"] == 1
    _, _, t1, status, exit_price, reason, gross, net = _trade(signals_db, sid)
    assert status == "closed" and reason == "hit_t1"
    assert exit_price == pytest.approx(103.0)        # filled at the T1 level
    assert gross > 0 and net < gross                  # costs applied


def test_hits_sl(signals_db):
    sid = _seed_signal(signals_db, price=100.0, atr=2.0)
    pt.open_new_paper_trades(signals_db, now=NOW)
    seed_quote(signals_db, "ACME", last_price=96.0, pct_change=-4.0, as_of=SESSION_START + 600)

    counts = pt.manage_open_trades(signals_db, now=NOW)
    assert counts["hit_sl"] == 1
    _, sl, _, status, exit_price, reason, gross, net = _trade(signals_db, sid)
    assert status == "closed" and reason == "hit_sl"
    assert exit_price == pytest.approx(97.0)          # filled at the SL level
    assert gross < 0


def test_stays_open_between_levels(signals_db):
    _seed_signal(signals_db, price=100.0, atr=2.0)
    pt.open_new_paper_trades(signals_db, now=NOW)
    seed_quote(signals_db, "ACME", last_price=101.0, pct_change=1.0, as_of=SESSION_START + 600)
    counts = pt.manage_open_trades(signals_db, now=NOW)
    assert counts == {"hit_t1": 0, "hit_sl": 0, "forced_flat": 0}


def test_force_flat_at_1520(signals_db):
    sid = _seed_signal(signals_db, price=100.0, atr=2.0)
    pt.open_new_paper_trades(signals_db, now=NOW)
    seed_quote(signals_db, "ACME", last_price=101.0, pct_change=1.0, as_of=SESSION_START + 600)

    flat_time = datetime(2025, 6, 2, 15, 20, 0, tzinfo=IST)
    counts = pt.manage_open_trades(signals_db, now=flat_time)
    assert counts["forced_flat"] == 1
    _, _, _, status, exit_price, reason, *_ = _trade(signals_db, sid)
    assert status == "closed" and reason == "forced_flat"
    assert exit_price == pytest.approx(101.0)         # filled at last price


def test_full_pass_opens_then_holds(signals_db):
    _seed_signal(signals_db, price=100.0, atr=2.0)
    seed_quote(signals_db, "ACME", last_price=101.0, pct_change=1.0, as_of=SESSION_START + 600)
    report = pt.run_paper_tracker_pass(signals_db, now=NOW)
    assert report["opened"] == 1
    assert report["hit_t1"] == 0 and report["hit_sl"] == 0


def test_pass_skips_when_market_closed(signals_db):
    closed = datetime(2025, 6, 2, 16, 0, 0, tzinfo=IST)
    assert pt.run_paper_tracker_pass(signals_db, now=closed) == {"skipped": "market_closed"}
