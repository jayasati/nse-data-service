"""
Pre-market loader — runs at 08:45 IST on trading days, before the 09:15 open.

Three jobs, all so the live machinery starts the session warm rather than cold:

  1. **Seed `indicator_live`.** Build each universe symbol's snapshot from
     end-of-day data (yesterday's close, daily ATR, SMA regime, the prior
     session's final RSI). VWAP and the 5-min ATR are NULL until today's bars
     print. This means the table is never empty at the open — the signal engine
     and dashboard read sensible "carry-over" values from the first tick.

  2. **Publish the blacklist.** Symbols under NSE surveillance (GSM / ASM short
     & long term) shouldn't be traded; we union them into a Redis set so the
     signal engine can exclude them with one membership check.

  3. **Publish per-symbol quality flags.** How much history each symbol has
     (daily bar count, whether SMA-200 is computable) plus its blacklist state,
     so downstream code can gate on data sufficiency without re-querying SQLite.

Both Redis writes carry a 6-hour TTL — long enough to cover the trading day,
short enough that a stale set never survives into the next session if the
loader is skipped (a holiday the gate catches, or the laptop being asleep).

The original checklist also mentions warming a RAM cache of the last 250
bhavcopy rows per symbol. That cache has no consumer yet (the signal engine
that would read it is a later phase), so it is intentionally deferred rather
than built dead — seeding `indicator_live` covers the "warm at open" intent.

Registered from main.py via `register_pre_market_loader`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import structlog
from apscheduler.triggers.cron import CronTrigger

from ..scheduler import market_hours
from ..storage.db import open_db
from .live_snapshot import build_snapshot, write_snapshots
from .universe import live_universe

log = structlog.get_logger()

JOB_ID = "indicators_pre_market"

# Surveillance tables whose members make up the do-not-trade blacklist.
_SURVEILLANCE_TABLES = (
    "raw_surveillance_gsm",
    "raw_surveillance_asm_st",
    "raw_surveillance_asm_lt",
)

_BLACKLIST_KEY = "blacklist:symbols"
_REDIS_TTL_SECS = 6 * 3600


def run_pre_market_load(
    db_path: str,
    *,
    redis_client=None,
    now: datetime | None = None,
) -> dict:
    """Seed indicator_live + publish blacklist/quality. Returns a report dict."""
    now = now or market_hours.now_ist()
    conn = open_db(db_path)
    try:
        symbols = live_universe(conn)

        rows = [build_snapshot(conn, s, now=now) for s in symbols]
        seeded = write_snapshots(conn, rows)

        blacklist = compute_blacklist(conn)
        quality = compute_quality(conn, symbols, blacklist)
    finally:
        conn.close()

    published = False
    if redis_client is not None:
        try:
            write_blacklist_to_redis(redis_client, blacklist)
            write_quality_to_redis(redis_client, quality)
            published = True
        except Exception:
            log.exception("pre_market_redis_publish_failed")

    return {
        "symbols": len(symbols),
        "seeded": seeded,
        "blacklisted": len(blacklist),
        "redis_published": published,
    }


# ----------------------------------------------------------- blacklist / qa

def compute_blacklist(conn: sqlite3.Connection) -> set[str]:
    """Union of symbols currently under any tracked surveillance stage."""
    blacklist: set[str] = set()
    for table in _SURVEILLANCE_TABLES:
        if not _has_table(conn, table):
            continue
        blacklist.update(
            r[0] for r in conn.execute(f"SELECT DISTINCT symbol FROM {table}")
        )
    return blacklist


def compute_quality(
    conn: sqlite3.Connection,
    symbols: list[str],
    blacklist: set[str],
) -> dict[str, dict]:
    """Per-symbol data-sufficiency flags: daily bar count, SMA-200, blacklist."""
    daily_counts = _daily_bar_counts(conn)
    has_sma200 = _symbols_with_sma200(conn)
    return {
        s: {
            "daily_bars": daily_counts.get(s, 0),
            "has_sma200": 1 if s in has_sma200 else 0,
            "blacklisted": 1 if s in blacklist else 0,
        }
        for s in symbols
    }


def write_blacklist_to_redis(redis_client, blacklist: set[str], ttl: int = _REDIS_TTL_SECS) -> None:
    """Replace the blacklist set atomically (DEL then re-add) with a TTL."""
    pipe = redis_client.pipeline()
    pipe.delete(_BLACKLIST_KEY)
    if blacklist:
        pipe.sadd(_BLACKLIST_KEY, *blacklist)
        pipe.expire(_BLACKLIST_KEY, ttl)
    pipe.execute()


def write_quality_to_redis(redis_client, quality: dict[str, dict], ttl: int = _REDIS_TTL_SECS) -> None:
    """Write each symbol's flags to a Redis hash `quality:{symbol}` with a TTL."""
    pipe = redis_client.pipeline()
    for symbol, flags in quality.items():
        key = f"quality:{symbol}"
        pipe.hset(key, mapping=flags)
        pipe.expire(key, ttl)
    pipe.execute()


def _daily_bar_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT symbol, COUNT(*) FROM raw_bhavcopy_cm "
            "WHERE series = 'EQ' GROUP BY symbol"
        )
    }


def _symbols_with_sma200(conn: sqlite3.Connection) -> set[str]:
    if not _has_table(conn, "indicator_sma"):
        return set()
    return {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT symbol FROM indicator_sma WHERE sma_200 IS NOT NULL"
        )
    }


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None


# --------------------------------------------------------------- scheduling

def register_pre_market_loader(scheduler, db_path: str) -> str:
    """Attach the 08:45-IST pre-market loader to the scheduler.

    Fires daily; the runtime `is_trading_day` gate skips weekends and holidays
    (which a plain cron expression can't know about), matching the
    trading_day_only semantics used by the config-driven jobs.
    """
    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            log.info("pre_market_skipped_non_trading_day")
            return
        try:
            redis_client = _connect_redis()
            report = run_pre_market_load(db_path, redis_client=redis_client)
            log.info("pre_market_load", **report)
        except Exception:
            log.exception("pre_market_load_failed")

    scheduler.add_job(
        _tick,
        trigger=CronTrigger(hour=8, minute=45, timezone=market_hours.IST),
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return JOB_ID


def _connect_redis():
    """Best-effort Redis client; None if unavailable (publish is then skipped)."""
    try:
        import redis  # type: ignore

        client = redis.Redis(decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None
