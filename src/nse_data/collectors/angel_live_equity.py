"""Angel-sourced live quotes for the top-1000 names NSE doesn't serve.

NSE's free live feed (/api/equity-stock-indices) only covers index
constituents — the broadest is NIFTY 500 ∪ NIFTY SMALLCAP 500 (~750 symbols,
collected by live_equity.LiveEquityTotalMarket). The top-1000-by-turnover
universe includes ~337 names outside that set (ETFs like NIFTYBEES/GOLDBEES,
small/recent listings), which therefore had NO live intraday data — only the
Angel historical backfill.

This job fills that gap: every minute during market hours it polls those
symbols via Angel One SmartAPI (brokers.angel.fetch_quotes) and writes them to
raw_equity_quotes under the SAME `index_name` label the dashboard reads
(webcore.config.LIVE_INDEX). Because the gap symbols are disjoint from the
NSE-served set, there's no PK collision and the intraday 1-min builder
(indicators.intraday_ohlcv) synthesizes their live candles unchanged — it
neither knows nor cares that these rows came from the broker instead of NSE.

Symbol list: config/angel_live_symbols.txt (top-1000 minus the NSE-served
universe), one symbol per line.

    run_angel_live_pass(conn, symbols)              # poll + write one snapshot
    register_angel_live_job(scheduler, db_path)     # every 60s, market hours
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import structlog

from ..webcore.config import LIVE_INDEX

log = structlog.get_logger()

JOB_ID = "angel_live_equity"
_INTERVAL_SECONDS = 60
_SYMBOLS_FILE = Path("config/angel_live_symbols.txt")


def load_gap_symbols(path: Path = _SYMBOLS_FILE) -> list[str]:
    """Top-1000 symbols NSE's live feed doesn't serve (one per line)."""
    if not path.exists():
        return []
    return [l.strip().upper() for l in path.read_text().splitlines() if l.strip()]


def run_angel_live_pass(
    conn: sqlite3.Connection, symbols: list[str], *, now: int | None = None,
    fetcher=None,
) -> dict:
    """Poll `symbols` for live quotes and upsert one snapshot into
    raw_equity_quotes (index_name=LIVE_INDEX). `fetcher(symbols) -> [{...}]` is
    injectable for tests; defaults to brokers.angel.fetch_quotes."""
    if not symbols:
        return {"symbols": 0, "written": 0}
    if fetcher is None:
        from ..brokers import angel
        fetcher = angel.fetch_quotes

    as_of = now if now is not None else int(time.time())
    quotes = fetcher(symbols)
    rows = [
        (q["symbol"], as_of, LIVE_INDEX, q.get("last_price"), q.get("open"),
         q.get("day_high"), q.get("day_low"), q.get("prev_close"), q.get("volume"))
        for q in quotes if q.get("last_price") is not None
    ]
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO raw_equity_quotes "
            "(symbol, as_of, index_name, last_price, open, day_high, day_low, "
            " prev_close, volume) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    report = {"symbols": len(symbols), "fetched": len(quotes), "written": len(rows)}
    log.info("angel_live_pass", **report)
    return report


def register_angel_live_job(scheduler, db_path: str) -> str:
    """Every 60s during market hours: poll the NSE-gap symbols via Angel."""
    from apscheduler.triggers.interval import IntervalTrigger

    from ..brokers import angel
    from ..scheduler.market_hours import is_market_open
    from ..storage.db import open_db

    symbols = load_gap_symbols()
    if not symbols:
        log.warning("angel_live_no_symbols", hint=f"missing/empty {_SYMBOLS_FILE}")
    if not angel.credentials_present():
        log.warning("angel_live_no_creds", hint="ANGEL_* not set; job will no-op")

    def _tick():
        if not symbols or not is_market_open():
            return
        if not angel.credentials_present():
            return
        conn = open_db(db_path)
        try:
            run_angel_live_pass(conn, symbols)
        except Exception as e:
            log.error("angel_live_failed", err=repr(e))   # repr renders in the JSON event (exc_info did not)
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(seconds=_INTERVAL_SECONDS),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True,
    )
    return JOB_ID
