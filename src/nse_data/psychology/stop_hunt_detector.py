"""Stop-hunt / liquidity-grab detector (FEATURE_CHECKLIST Week 21, task 21.1).

A liquidity grab: price spikes BELOW an obvious support (prior-day low, round number,
recent swing low) on a volume surge, then snaps BACK ABOVE it as the stops it triggered
provide liquidity for larger buyers — and volume cools after the recovery. The setup is a
LIQUIDITY_GRAB_LONG with the stop tucked under the wick low and entry on the reclaim.

    grab candle : low < support  AND  volume > vol_mult × avg(other recent candles)
    recovery    : that candle or the next CLOSES back above support
    confirmation: volume drops on the candle after the recovery

Pure `detect_stop_hunt` (5-min candles) is unit-tested; the pass scans the live universe on
5-min closes against the day's support levels. Feeds the gated dispatch path like every signal.
"""
from __future__ import annotations

import sqlite3

import structlog

from ..scheduler.market_hours import is_market_open, now_ist

log = structlog.get_logger()

JOB_ID = "psychology_stop_hunt"
LIQUIDITY_GRAB_LONG = "liquidity_grab_long"
_INTERVAL_SECONDS = 300


def detect_stop_hunt(bars: list[dict], support: float | None, *,
                     vol_mult: float = 2.0, lookback: int = 6) -> dict | None:
    """A liquidity grab in the last `lookback` 5-min bars around `support`, else None.

    `bars` are dicts with low/high/close/volume, oldest→newest.
    """
    if support is None or support <= 0 or len(bars) < 3:
        return None
    recent = bars[-lookback:]
    vols = [float(b.get("volume") or 0) for b in recent]
    for i, b in enumerate(recent):
        others = [vols[k] for k in range(len(recent)) if k != i and vols[k] > 0]
        avg = sum(others) / len(others) if others else 0.0
        if not (b["low"] < support and avg > 0 and vols[i] > vol_mult * avg):
            continue
        rec = (i if b["close"] > support
               else i + 1 if (i + 1 < len(recent) and recent[i + 1]["close"] > support)
               else None)
        if rec is None:
            continue
        if rec + 1 < len(recent) and vols[rec + 1] < vols[i]:          # volume cools after reclaim
            wick_low = min(recent[k]["low"] for k in range(i, rec + 1))
            return {"support": support, "wick_low": round(wick_low, 2),
                    "entry": round(recent[rec]["close"], 2),
                    "sl": round(wick_low * 0.999, 2)}
    return None


def _support(conn: sqlite3.Connection, symbol: str) -> float | None:
    """The nearest obvious support: prior-day low, else pivot S1."""
    try:
        r = conn.execute(
            "SELECT pdl, s1 FROM indicator_levels WHERE symbol=? ORDER BY session_date DESC LIMIT 1",
            (symbol,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not r:
        return None
    return r[0] or r[1]


def _recent_5m(conn: sqlite3.Connection, symbol: str, now) -> list[dict]:
    from ..indicators.intraday_ohlcv import read_intraday_5m
    session_open = int(now.replace(hour=9, minute=15, second=0, microsecond=0).timestamp())
    bars = read_intraday_5m(conn, symbol, since_ts=session_open)
    if bars is None or bars.empty:
        return []
    return [{"low": float(r.low), "high": float(r.high), "close": float(r.close),
             "volume": float(r.volume)} for r in bars.itertuples()]


def run_stop_hunt_pass(conn, *, now=None, symbols: list[str] | None = None) -> dict:
    from ..indicators.universe import live_universe
    now = now or now_ist()
    symbols = symbols if symbols is not None else live_universe(conn)
    hits = []
    for sym in symbols:
        try:
            grab = detect_stop_hunt(_recent_5m(conn, sym, now), _support(conn, sym))
        except Exception:  # noqa: BLE001 — one bad symbol shouldn't kill the pass
            log.exception("stop_hunt_failed", symbol=sym)
            continue
        if grab:
            hits.append({"symbol": sym, **grab})
            log.info("liquidity_grab", symbol=sym, support=grab["support"])
    return {"symbols": len(symbols), "grabs": len(hits), "hits": hits}


def register_stop_hunt_detector_job(scheduler, db_path: str) -> str:
    """Every 5 min during market hours (checks on 5-min candle closes, task 21.1)."""
    from apscheduler.triggers.interval import IntervalTrigger

    from ..storage.db import open_db

    def _tick():
        if not is_market_open():
            return
        conn = open_db(db_path)
        try:
            rep = run_stop_hunt_pass(conn)
            if rep["grabs"]:
                log.info("stop_hunt_tick", **rep)
        except Exception:
            log.exception("stop_hunt_tick_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(seconds=_INTERVAL_SECONDS),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True)
    return JOB_ID
