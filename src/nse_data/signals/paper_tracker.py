"""
Paper-trade tracker (FEATURE_CHECKLIST Week 5, tasks 5.3 + 5.4).

Runs every minute during market hours and does three things:

  1. **Open** a paper trade for each new signal that doesn't have one yet.
     Entry = the signal's detection price; SL/T1 are ATR-based (task 5.4):
         SL = entry − 1.5 × atr_14_daily
         T1 = entry + 1.5 × atr_14_daily   (1R target)
     Paper trades start from day one for *every* signal — independent of the
     Telegram confidence gate — so the eval/ML dataset is complete.

  2. **Check** each open trade against the latest `raw_equity_quotes` price. If
     last price ≥ T1 → close 'hit_t1'; if ≤ SL → close 'hit_sl'. Closes are
     priced at the level (we assume a fill at T1/SL) and run through the full
     cost model (costs/model.py) for net P&L.

  3. **Force-flat** every still-open trade at 15:20 IST ('forced_flat', exit at
     last price) — Phase 1 is intraday-only, nothing carries overnight.

Phase-1 simplifications (revisited later): longs only (both Phase-1 signals are
longs); fixed notional sizing; hit detection on last price only (no intrabar
high/low), so a wick that pierces and recovers inside one minute is missed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, time

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..costs.model import compute_costs
from ..scheduler.market_hours import is_market_open, now_ist
from ..storage.db import open_db

log = structlog.get_logger()

JOB_ID = "signals_paper_tracker"
_INTERVAL_SECONDS = 60

# Task 5.4: ATR-based bracket. 1.5×ATR each side -> a 1R (1:1) target.
ATR_SL_MULT = 1.5

# Phase-1 fixed-notional sizing. Position size = floor(capital / entry), ≥ 1.
# Phase 8 replaces this with risk-based sizing off the SL distance.
PAPER_CAPITAL_PER_TRADE = 100_000.0

# Intraday-only: flat everything by 15:20 so nothing is held into the close.
FORCE_FLAT_TIME = time(15, 20)


# ============================================================================
# Pass orchestration
# ============================================================================

def run_paper_tracker_pass(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict:
    """One tracker tick: open new trades, manage open ones. Returns a report."""
    now = now or now_ist()
    if not is_market_open(now):
        return {"skipped": "market_closed"}

    opened = open_new_paper_trades(conn, now=now)
    managed = manage_open_trades(conn, now=now)
    return {"opened": opened, **managed}


# ============================================================================
# 1. Open trades for new signals (tasks 5.3 entry, 5.4 bracket)
# ============================================================================

def open_new_paper_trades(conn: sqlite3.Connection, *, now: datetime) -> int:
    """Open one paper trade per today's signal that doesn't have one yet.

    Skips a signal whose entry price or daily ATR is missing — without both we
    can't place a managed bracket. Returns the number of trades opened.
    """
    day = now.date().isoformat()
    rows = conn.execute(
        "SELECT s.id, s.symbol, s.signal_type, s.price, sf.atr_14_daily, "
        "       COALESCE(s.direction, 'long') "
        "FROM signals s "
        "LEFT JOIN paper_trades pt ON pt.signal_id = s.id "
        "LEFT JOIN signal_features sf ON sf.signal_id = s.id "
        "WHERE pt.id IS NULL AND substr(s.detected_at, 1, 10) = ?",
        (day,),
    ).fetchall()

    opened = 0
    for signal_id, symbol, signal_type, entry_price, atr, direction in rows:
        if entry_price is None or atr is None or atr <= 0:
            continue
        sl, t1 = compute_sl_t1(entry_price, atr, direction)
        conn.execute(
            "INSERT INTO paper_trades "
            "(signal_id, symbol, signal_type, entry_price, sl_price, t1_price, "
            " entry_time, status, direction) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
            (signal_id, symbol, signal_type, entry_price, sl, t1, now.isoformat(), direction),
        )
        opened += 1
    conn.commit()
    return opened


def compute_sl_t1(
    entry_price: float, atr_14_daily: float, direction: str = "long",
) -> tuple[float, float]:
    """ATR bracket (task 5.4): 1.5×ATR each side. For a long, (SL, T1) =
    entry ∓ band; for a short the bracket flips, (SL, T1) = entry ± band (stop
    above entry, target below)."""
    band = ATR_SL_MULT * atr_14_daily
    if direction == "short":
        return (entry_price + band, entry_price - band)
    return (entry_price - band, entry_price + band)


def _position_size(entry_price: float) -> int:
    """Fixed-notional Phase-1 sizing: floor(capital / entry), at least 1 share."""
    return max(1, int(PAPER_CAPITAL_PER_TRADE // entry_price))


# ============================================================================
# 2 + 3. Manage open trades: T1/SL hits, then 15:20 force-flat
# ============================================================================

def manage_open_trades(conn: sqlite3.Connection, *, now: datetime) -> dict:
    """Close open trades that hit T1/SL; force-flat the rest at 15:20."""
    force_flat = now.timetz().replace(tzinfo=None) >= FORCE_FLAT_TIME
    open_trades = conn.execute(
        "SELECT id, symbol, entry_price, sl_price, t1_price, COALESCE(direction, 'long') "
        "FROM paper_trades WHERE status = 'open'"
    ).fetchall()

    counts = {"hit_t1": 0, "hit_sl": 0, "forced_flat": 0}
    for trade_id, symbol, entry, sl, t1, direction in open_trades:
        last_price = _latest_price(conn, symbol)

        reason, exit_price = _exit_decision(last_price, sl, t1, force_flat, direction)
        if reason is None:
            continue

        _close_trade(conn, trade_id, entry, exit_price, reason, now, direction)
        counts[reason] += 1

    conn.commit()
    return counts


def _exit_decision(
    last_price: float | None, sl: float, t1: float, force_flat: bool,
    direction: str = "long",
) -> tuple[str | None, float]:
    """Decide whether/why an open trade closes this tick, and at what price.

    Long: T1 above, SL below (hit on last >= t1 / <= sl). Short: T1 below, SL
    above (hit on last <= t1 / >= sl). Force-flat fills at last price.
    Returns (reason, exit_price); reason None = stay open.
    """
    if last_price is not None:
        if direction == "short":
            if last_price <= t1:
                return ("hit_t1", t1)
            if last_price >= sl:
                return ("hit_sl", sl)
        else:
            if last_price >= t1:
                return ("hit_t1", t1)
            if last_price <= sl:
                return ("hit_sl", sl)
    if force_flat and last_price is not None:
        return ("forced_flat", last_price)
    return (None, 0.0)


def _close_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    entry_price: float,
    exit_price: float,
    reason: str,
    now: datetime,
    direction: str = "long",
) -> None:
    """Write the close: gross/net P&L (full cost model), exit fields, status."""
    quantity = _position_size(entry_price)
    costs = compute_costs(entry_price, exit_price, quantity, trade_type=direction)
    conn.execute(
        "UPDATE paper_trades SET exit_price = ?, exit_time = ?, exit_reason = ?, "
        "gross_pnl = ?, net_pnl = ?, status = 'closed' WHERE id = ?",
        (exit_price, now.isoformat(), reason,
         costs.gross_pnl, costs.net_pnl, trade_id),
    )


def _latest_price(conn: sqlite3.Connection, symbol: str) -> float | None:
    row = conn.execute(
        "SELECT last_price FROM raw_equity_quotes WHERE symbol = ? "
        "ORDER BY as_of DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return row[0] if row and row[0] is not None else None


# ============================================================================
# Scheduling
# ============================================================================

def run_paper_tracker_job(db_path: str) -> dict:
    if not is_market_open():
        return {"skipped": "market_closed"}
    conn = open_db(db_path)
    try:
        return run_paper_tracker_pass(conn)
    finally:
        conn.close()


def register_paper_tracker(scheduler: BlockingScheduler, db_path: str) -> str:
    """Attach the every-minute paper-trade tracker to the scheduler."""
    def _tick():
        try:
            report = run_paper_tracker_job(db_path)
            if "skipped" not in report:
                log.info("paper_tracker_tick", **report)
        except Exception:
            log.exception("paper_tracker_failed")

    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(seconds=_INTERVAL_SECONDS),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return JOB_ID
