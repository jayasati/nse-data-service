"""Keep the daily-candle table current from the official NSE bhavcopy.

The broker historical feed (Angel/Kite) that backfilled raw_intraday_candles[interval='day'] can
lag or stop; the EOD bhavcopy (raw_bhavcopy_cm) is collected reliably every session and is the
authoritative daily OHLCV. This syncs bhavcopy → daily candles so every consumer (conviction
structure/RS/volume, backtests, …) scores on fresh bars without depending on the broker feed.

NSE re-serves the prior Friday's bhavcopy on Sat/Sun under the weekend date, so those rows are
phantom duplicates — skipped here. Bar ts = trade-date 00:00 IST (the existing convention).
"""
from __future__ import annotations

import datetime as dt

import structlog

log = structlog.get_logger()
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def sync_bhavcopy_to_daily(conn, lookback_days: int = 15) -> dict:
    """Upsert recent EOD bhavcopy bars into raw_intraday_candles[interval='day']. Idempotent."""
    cutoff = (dt.datetime.now(IST).date() - dt.timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        "SELECT symbol, date, open, high, low, close, volume FROM raw_bhavcopy_cm "
        "WHERE series='EQ' AND date>=? AND open IS NOT NULL AND close IS NOT NULL",
        (cutoff,)).fetchall()
    batch, weekend = [], 0
    for sym, d, o, h, l, cl, v in rows:
        day = dt.date.fromisoformat(d)
        if day.weekday() >= 5:                       # Sat/Sun = re-served Friday file, skip
            weekend += 1
            continue
        ts = int(dt.datetime(day.year, day.month, day.day, tzinfo=IST).timestamp())
        batch.append((sym, "day", ts, o, h, l, cl, v, "bhavcopy"))
    if batch:
        conn.executemany(
            "INSERT OR REPLACE INTO raw_intraday_candles (symbol, interval, ts, open, high, low, "
            "close, volume, source) VALUES (?,?,?,?,?,?,?,?,?)", batch)
        conn.commit()
    return {"synced": len(batch), "weekend_skipped": weekend, "since": cutoff}


def register_bhavcopy_candle_job(scheduler, db_path: str) -> str:
    """19:30 IST — after the EOD bhavcopy is collected, refresh the daily-candle table."""
    from apscheduler.triggers.cron import CronTrigger

    from ..scheduler import market_hours
    from ..storage.db import open_db

    def _tick():
        conn = open_db(db_path)
        try:
            log.info("bhavcopy_candles", **sync_bhavcopy_to_daily(conn))
        except Exception:
            log.exception("bhavcopy_candles_failed")
        finally:
            conn.close()

    scheduler.add_job(_tick, trigger=CronTrigger(hour=19, minute=30, timezone=market_hours.IST),
                      id="bhavcopy_candles", max_instances=1, coalesce=True, replace_existing=True)
    return "bhavcopy_candles"
