"""
Dynamic live watchlist (Phase 4 — focused universe).

Keeps `live_watchlist` populated so the intraday jobs compute indicators for the
~200-name core PLUS any name that recently earned attention. Triggers:

    rating          — announcement subject mentions a (credit) rating change
    news            — high-priority announcement (or sentiment-flagged, server-side)
    oi_spurt        — unusual derivatives activity (raw_oi_spurts)
    breakout_52wh   — fresh 52-week high (raw_high_low_52w, penny tiers excluded)

Each trigger sets the symbol's expiry to `ttl_trading_days` ahead; a re-trigger
pushes it out again. Expired rows are pruned each pass, so the live set stays
small. Read side lives in indicators.universe (active_watchlist / live_universe).

Runs every 15 min via `register_watchlist_job` — not market-hours-gated, so the
evening's rating/news announcements land on the watchlist before the next open.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..scheduler import market_hours
from ..storage.db import open_db

log = structlog.get_logger()

JOB_ID = "live_watchlist"
_INTERVAL_SECONDS = 900
_TTL_TRADING_DAYS = 5
_LOOKBACK_HOURS = 30          # ~one trading day of triggers


def _trading_days_ahead(now: datetime, n: int) -> datetime:
    d = now
    added = 0
    for _ in range(n * 3 + 5):   # bounded walk past weekends/holidays
        d += timedelta(days=1)
        if market_hours.is_trading_day(d.date()):
            added += 1
            if added >= n:
                break
    return d


def add_to_watchlist(
    conn: sqlite3.Connection, symbol: str, reason: str,
    now_iso: str, expires_iso: str,
) -> None:
    """Upsert one symbol; a re-trigger only ever extends the expiry."""
    conn.execute(
        "INSERT INTO live_watchlist (symbol, reason, added_at, expires_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET "
        "  reason = excluded.reason, "
        "  expires_at = MAX(live_watchlist.expires_at, excluded.expires_at)",
        (symbol, reason, now_iso, expires_iso),
    )


# ---- trigger scanners (each returns a set of symbols) ----------------------

def _has(conn, name) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _rating_symbols(conn, cutoff_epoch) -> set[str]:
    if not _has(conn, "raw_announcements"):
        return set()
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM raw_announcements "
        "WHERE created_at >= ? AND LOWER(subject) LIKE '%rating%'",
        (cutoff_epoch,),
    )}


def _news_symbols(conn, cutoff_epoch) -> set[str]:
    if not _has(conn, "raw_announcements"):
        return set()
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM raw_announcements "
        "WHERE created_at >= ? AND (LOWER(priority) = 'high' OR "
        "      LOWER(COALESCE(sentiment,'')) IN "
        "      ('positive','negative','very_positive','very_negative'))",
        (cutoff_epoch,),
    )}


def _oi_spurt_symbols(conn, cutoff_epoch) -> set[str]:
    if not _has(conn, "raw_oi_spurts"):
        return set()
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM raw_oi_spurts WHERE as_of >= ?",
        (cutoff_epoch,),
    )}


def _breakout_symbols(conn, cutoff_epoch) -> set[str]:
    if not _has(conn, "raw_high_low_52w"):
        return set()
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM raw_high_low_52w "
        "WHERE as_of >= ? AND event = 'high' AND COALESCE(price_tier,'') != 'lte20'",
        (cutoff_epoch,),
    )}


# ---- pass ------------------------------------------------------------------

def refresh_watchlist(
    conn: sqlite3.Connection, *,
    now: datetime | None = None,
    ttl_trading_days: int = _TTL_TRADING_DAYS,
    lookback_hours: int = _LOOKBACK_HOURS,
) -> dict[str, int]:
    """Scan triggers, upsert symbols with a fresh expiry, prune expired rows."""
    now = now or market_hours.now_ist()
    now_iso = now.isoformat()
    expires_iso = _trading_days_ahead(now, ttl_trading_days).isoformat()
    cutoff = int((now - timedelta(hours=lookback_hours)).timestamp())

    by_reason = {
        "rating": _rating_symbols(conn, cutoff),
        "news": _news_symbols(conn, cutoff),
        "oi_spurt": _oi_spurt_symbols(conn, cutoff),
        "breakout_52wh": _breakout_symbols(conn, cutoff),
    }
    counts = {}
    for reason, symbols in by_reason.items():
        for sym in symbols:
            add_to_watchlist(conn, sym, reason, now_iso, expires_iso)
        counts[reason] = len(symbols)

    conn.execute("DELETE FROM live_watchlist WHERE expires_at <= ?", (now_iso,))
    conn.commit()
    counts["active"] = conn.execute(
        "SELECT COUNT(*) FROM live_watchlist WHERE expires_at > ?", (now_iso,)
    ).fetchone()[0]
    return counts


# ---- scheduling ------------------------------------------------------------

def run_watchlist_job(db_path: str) -> dict:
    conn = open_db(db_path)
    try:
        return refresh_watchlist(conn)
    finally:
        conn.close()


def register_watchlist_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Attach the 15-min watchlist refresh. Not market-hours-gated (evening
    rating/news must land before the next open)."""
    def _tick():
        try:
            report = run_watchlist_job(db_path)
            log.info("live_watchlist_tick", **report)
        except Exception:
            log.exception("live_watchlist_failed")

    scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(seconds=_INTERVAL_SECONDS),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True,
    )
    return JOB_ID
