"""
Intraday pattern detection (FEATURE_CHECKLIST Phase 4, Week 15, tasks 15.1/15.2).

A per-minute job that scans the live universe for simple, well-defined patterns
and writes them to `patterns`. Pure detectors (top) are unit-tested; the pass
glues them to the 5-min bars + levels + RSI already in the DB.

Patterns:
    inside_bar          today's range inside the prior day's (pdh/pdl)
    volume_dryup        current 5m volume < 50% of the 20-bar average
    near_support        price within 0.5% of S1/S2
    near_resistance     price within 0.5% of R1/R2
    higher_high         last 5m high > prior 5m high
    lower_low           last 5m low  < prior 5m low
    bullish_divergence  price lower-low while RSI higher-low (last 10 bars)
    bearish_divergence  price higher-high while RSI lower-high
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..scheduler import market_hours
from ..scheduler.market_hours import is_market_open
from ..storage.db import open_db
from .intraday_ohlcv import read_intraday_5m

log = structlog.get_logger()
JOB_ID = "patterns"
_INTERVAL_SECONDS = 60
_NEAR_PCT = 0.005          # 0.5%
_VOL_DRYUP = 0.5           # < 50% of 20-bar avg
_VOL_WINDOW = 20
_DIVERGENCE_LOOKBACK = 10


# ---- pure detectors --------------------------------------------------------

def is_inside_bar(today_high, today_low, prev_high, prev_low) -> bool:
    if None in (today_high, today_low, prev_high, prev_low):
        return False
    return today_high < prev_high and today_low > prev_low


def is_volume_dryup(cur_vol, avg20_vol) -> bool:
    return bool(avg20_vol and cur_vol is not None and cur_vol < _VOL_DRYUP * avg20_vol)


def near(price, level, pct: float = _NEAR_PCT) -> bool:
    if price is None or level is None or price <= 0:
        return False
    return abs(price - level) / price <= pct


def detect_divergence(prices: list[float], rsis: list[float],
                      lookback: int = _DIVERGENCE_LOOKBACK) -> str | None:
    """Bullish: price lower-low but RSI higher-low. Bearish: mirror."""
    if len(prices) < lookback or len(rsis) < lookback:
        return None
    p, r = prices[-lookback:], rsis[-lookback:]
    if p[-1] <= min(p[:-1]) and r[-1] > min(r[:-1]):
        return "bullish_divergence"
    if p[-1] >= max(p[:-1]) and r[-1] < max(r[:-1]):
        return "bearish_divergence"
    return None


# ---- per-symbol detection --------------------------------------------------

def detect_symbol_patterns(conn: sqlite3.Connection, symbol: str, now: datetime) -> list[tuple[str, float | None]]:
    """Return [(pattern_type, detail), …] detected for `symbol` right now."""
    bars = read_intraday_5m(conn, symbol, since_ts=int(now.timestamp()) - 3 * 3600)
    if bars.empty or len(bars) < 2:
        return []

    highs = bars["high"].tolist()
    lows = bars["low"].tolist()
    vols = bars["volume"].tolist()
    closes = bars["close"].tolist()
    price = closes[-1]

    found: list[tuple[str, float | None]] = []

    # higher-high / lower-low (last vs prior 5m bar)
    if highs[-1] > highs[-2]:
        found.append(("higher_high", highs[-1]))
    if lows[-1] < lows[-2]:
        found.append(("lower_low", lows[-1]))

    # volume dry-up vs 20-bar avg
    window = vols[-(_VOL_WINDOW + 1):-1] or vols[:-1]
    if window:
        avg20 = sum(window) / len(window)
        if is_volume_dryup(vols[-1], avg20):
            found.append(("volume_dryup", round(vols[-1] / avg20, 3) if avg20 else None))

    # inside bar: today's session range inside prior day (pdh/pdl from levels)
    levels = _levels(conn, symbol)
    if levels:
        today_high, today_low = max(highs), min(lows)
        if is_inside_bar(today_high, today_low, levels.get("pdh"), levels.get("pdl")):
            found.append(("inside_bar", None))
        for lv in ("s1", "s2"):
            if near(price, levels.get(lv)):
                found.append(("near_support", levels[lv]))
                break
        for lv in ("r1", "r2"):
            if near(price, levels.get(lv)):
                found.append(("near_resistance", levels[lv]))
                break

    # RSI–price divergence
    rsis = _rsi_series(conn, symbol, _DIVERGENCE_LOOKBACK)
    div = detect_divergence(closes, rsis)
    if div:
        found.append((div, None))

    return found


def run_patterns_pass(conn: sqlite3.Connection, symbols, *, now: datetime | None = None) -> dict:
    now = now or market_hours.now_ist()
    session_date = now.date().isoformat()
    ts = int(now.timestamp())
    counts: dict[str, int] = {}
    for sym in symbols:
        for ptype, detail in detect_symbol_patterns(conn, sym, now):
            conn.execute(
                "INSERT OR REPLACE INTO patterns "
                "(symbol, pattern_type, ts, session_date, detail, detected_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sym, ptype, ts, session_date, detail, now.isoformat()),
            )
            counts[ptype] = counts.get(ptype, 0) + 1
    conn.commit()
    return counts


# ---- reads -----------------------------------------------------------------

def _has(conn, name) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _levels(conn: sqlite3.Connection, symbol: str) -> dict | None:
    if not _has(conn, "indicator_levels"):
        return None
    row = conn.execute(
        "SELECT pdh, pdl, s1, s2, r1, r2 FROM indicator_levels "
        "WHERE symbol = ? ORDER BY session_date DESC LIMIT 1", (symbol,),
    ).fetchone()
    if not row:
        return None
    return dict(zip(("pdh", "pdl", "s1", "s2", "r1", "r2"), row))


def _rsi_series(conn: sqlite3.Connection, symbol: str, n: int) -> list[float]:
    if not _has(conn, "indicator_rsi_5m"):
        return []
    rows = conn.execute(
        "SELECT rsi_14 FROM indicator_rsi_5m WHERE symbol = ? AND rsi_14 IS NOT NULL "
        "ORDER BY ts DESC LIMIT ?", (symbol, n),
    ).fetchall()
    return [r[0] for r in reversed(rows)]


# ---- scheduling ------------------------------------------------------------

def run_patterns_job(db_path: str) -> dict:
    if not is_market_open():
        return {"skipped": "market_closed"}
    from .universe import live_universe
    conn = open_db(db_path)
    try:
        return run_patterns_pass(conn, live_universe(conn))
    finally:
        conn.close()


def register_patterns_job(scheduler: BlockingScheduler, db_path: str) -> str:
    """Per-minute pattern scan during market hours (task 15.1)."""
    def _tick():
        try:
            report = run_patterns_job(db_path)
            if "skipped" not in report and report:
                log.info("patterns_tick", **report)
        except Exception:
            log.exception("patterns_failed")

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(seconds=_INTERVAL_SECONDS),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True,
    )
    return JOB_ID
