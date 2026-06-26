"""Forward-validation paper track for the REFINED FPI signal (SHORT side).

The combined backtest inverted the naive hypothesis: FPI-headwind names that are already broken
down are oversold and bounce; the genuine underperformers are FPI-headwind names whose uptrend is
STILL INTACT (not yet broken down) — they fell -0.67% excess over the next fortnight (62% of the
time). This shorts that set on paper to see if it holds out of sample before it's ever trusted live.

Entry: a name tagged FPI_SECTOR_HEADWIND by a FRESH NSDL fortnight report (created_at < 3d) that is
in the tradeable universe and NOT in a swing downtrend (close >= SMA20 OR SMA20 >= SMA50). Short at
bhavcopy close, 5% stop (adverse = price up), ~fortnight max hold. Net-of-cost. NOT auto-scored.
"""
from __future__ import annotations

import sqlite3
import time

import structlog

from ..costs.model import compute_costs

log = structlog.get_logger(__name__)

STRATEGY = "fpi_headwind_short"
CAPITAL = 1_000_000
POS_PCT = 0.01
STOP_PCT = 5.0           # adverse (price UP) stop for a short
MAX_HOLD_DAYS = 17       # ~ one fortnight (≈12 trading days)


def _latest_bhav_date(conn) -> str | None:
    r = conn.execute("SELECT MAX(date) FROM raw_bhavcopy_cm").fetchone()
    return r[0] if r else None


def _is_breakdown(conn, symbol: str, date: str) -> bool | None:
    """Swing downtrend: close < SMA20 and SMA20 < SMA50 (daily bhavcopy). None if <50d history."""
    closes = [r[0] for r in conn.execute(
        "SELECT close FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' AND date<=? AND close>0 "
        "ORDER BY date DESC LIMIT 50", (symbol, date))]
    if len(closes) < 50:
        return None
    return closes[0] < sum(closes[:20]) / 20 and sum(closes[:20]) / 20 < sum(closes) / 50


def _fresh_headwind(conn) -> list[str]:
    """Names tagged FPI_SECTOR_HEADWIND by a fresh NSDL report (created_at within 3 days)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                        "name='fpi_sector_stock'").fetchone():
        return []
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM fpi_sector_stock WHERE signal='FPI_SECTOR_HEADWIND' "
        "AND created_at >= datetime('now','-3 day')")]


def run_pass(conn: sqlite3.Connection, *, date: str | None = None) -> dict:
    date = date or _latest_bhav_date(conn)
    if not date:
        return {"error": "no bhavcopy"}
    now = int(time.time())
    opened = closed = 0

    # 1) exits — adverse (up) stop or fortnight max-hold
    for pid, sym, entry_date, entry_px, stop_px, qty in conn.execute(
            "SELECT id, symbol, entry_date, entry_px, stop_px, qty FROM paper_book "
            "WHERE status='open' AND strategy=?", (STRATEGY,)):
        bar = conn.execute("SELECT high, close FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' "
                           "AND date=?", (sym, date)).fetchone()
        if not bar:
            continue
        high, close = bar
        held = conn.execute("SELECT julianday(?)-julianday(?)", (date, entry_date)).fetchone()[0]
        exit_px = reason = None
        if stop_px and high is not None and high >= stop_px:     # short stop: price rallied
            exit_px, reason = stop_px, "stop"
        elif held >= MAX_HOLD_DAYS:
            exit_px, reason = close, "max_hold"
        if exit_px and qty and entry_px:
            tc = compute_costs(entry_px, exit_px, int(qty), "short", "delivery")
            conn.execute(
                "UPDATE paper_book SET status='closed', exit_date=?, exit_px=?, exit_reason=?, "
                "net_pct=?, net_pnl=?, updated_at=? WHERE id=?",
                (date, exit_px, reason, round(tc.net_pnl / (entry_px * qty) * 100, 2),
                 round(tc.net_pnl, 2), now, pid))
            closed += 1

    # 2) entries — fresh headwind names, tradeable, NOT broken down (uptrend intact), not held
    held_syms = {r[0] for r in conn.execute(
        "SELECT symbol FROM paper_book WHERE status='open' AND strategy=?", (STRATEGY,))}
    tradeable = {r[0] for r in conn.execute("SELECT symbol FROM tradeable_universe")}
    for sym in _fresh_headwind(conn):
        if sym in held_syms or sym not in tradeable:
            continue
        if _is_breakdown(conn, sym, date) is not False:   # require explicit not-broken-down (False)
            continue
        bar = conn.execute("SELECT close FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' "
                           "AND date=?", (sym, date)).fetchone()
        if not bar or not bar[0]:
            continue
        px = bar[0]
        qty = max(1, int(CAPITAL * POS_PCT / px))
        stop_px = round(px * (1 + STOP_PCT / 100.0), 2)       # short stop ABOVE entry
        conn.execute(
            "INSERT INTO paper_book (symbol, entry_date, entry_px, status, strategy, stop_px, qty, "
            "risk_rupees, direction, updated_at) VALUES (?,?,?,'open',?,?,?,?,'short',?)",
            (sym, date, px, STRATEGY, stop_px, qty, round(qty * px * STOP_PCT / 100.0, 2), now))
        opened += 1
    conn.commit()
    report = {"date": date, "opened": opened, "closed": closed}
    log.info("fpi_short_paper", **report)
    return report


def register_fpi_short_paper_job(scheduler, db_path: str) -> str:
    """Nightly 19:50 IST (after bhavcopy + the fpi_sector tagging). Trading-day + toggle gated."""
    from apscheduler.triggers.cron import CronTrigger

    from ..events.calendar import _feature_enabled
    from ..scheduler import market_hours
    from ..storage.db import open_db
    job_id = "fpi_short_paper"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        if not _feature_enabled("fpi_short_paper", True):
            return
        conn = open_db(db_path)
        try:
            run_pass(conn)
        except Exception:
            log.exception("fpi_short_paper_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=19, minute=50, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True)
    return job_id
