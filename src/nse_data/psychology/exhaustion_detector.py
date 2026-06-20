"""Exhaustion alerts — FOMO warning + capitulation watch (FEATURE_CHECKLIST Week 20,
tasks 20.4/20.5/20.6).

A thin layer on the Week-19 psychology classifier: it already tags each symbol's
`psych_state` on indicator_live, so this turns the two actionable extremes into alerts:

    FOMO_EUPHORIA  → FOMO_WARNING        "don't chase, smart money sells into this"
    CAPITULATION   → CAPITULATION_WATCH  "reversal zone forming; wait for stabilisation"

Dedup: one alert per symbol+type per 2-hour cooldown (20.6), so a state that persists
across ticks doesn't spam. Like every other signal layer these feed the GATED dispatch
path (G12), so they are logged/recorded now and only fire live once the alert door opens.

Pure message builders + `find_exhaustion` are unit-tested; the pass/job glue to SQLite + Redis.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

import structlog

from ..scheduler.market_hours import is_market_open, now_ist

log = structlog.get_logger()

JOB_ID = "psychology_exhaustion"
FOMO_WARNING = "fomo_warning"
CAPITULATION_WATCH = "capitulation_watch"
_COOLDOWN_SECS = 2 * 60 * 60        # 2-hour dedup (20.6)


def _f(x, suf=""):
    return "n/a" if x is None else f"{x:.0f}{suf}" if isinstance(x, (int, float)) else str(x)


def fomo_warning_message(symbol: str, m: dict) -> str:
    """Task 20.4 template."""
    res = m.get("resistance")
    tail = f"\nWatch for reversal near ₹{res:.2f}" if res else ""
    return (f"⚠ {symbol} — FOMO Warning\n"
            f"State: FOMO_EUPHORIA\n\n"
            f"{_f(m.get('consecutive_up_days'))} consecutive up days\n"
            f"RSI(5m): {_f(m.get('rsi_5m'))} (overbought extreme)\n"
            f"Price: {m.get('price_vs_vwap') or 'n/a'} VWAP\n\n"
            f"DO NOT CHASE. Smart money sells into this.{tail}")


def capitulation_watch_message(symbol: str, m: dict) -> str:
    """Task 20.5 template."""
    return (f"🟡 {symbol} — Capitulation Zone\n"
            f"State: CAPITULATION\n\n"
            f"{_f(m.get('consecutive_down_days'))} consecutive down days\n"
            f"RSI(5m): {_f(m.get('rsi_5m'))} (oversold extreme)\n"
            f"Price: {m.get('price_vs_vwap') or 'n/a'} VWAP\n"
            f"Delivery: {m.get('delivery_trend') or 'n/a'} (long-term holders exiting)\n\n"
            f"Potential reversal zone forming.\n"
            f"Wait for stabilisation + volume dry-up; enter only on a confirmed base "
            f"(RSI turns, volume falls).")


def _builder(alert_type: str):
    return fomo_warning_message if alert_type == FOMO_WARNING else capitulation_watch_message


def find_exhaustion(conn: sqlite3.Connection, *, now: _dt.datetime | None = None) -> list[dict]:
    """Alert dicts for symbols the classifier left tagged FOMO_EUPHORIA / CAPITULATION.
    No dedup here (the pass applies the cooldown); pure read so it's testable."""
    now = now or now_ist()
    try:
        rows = conn.execute(
            "SELECT symbol, psych_state, consecutive_up_days, consecutive_down_days, rsi_5m, "
            "price_vs_vwap FROM indicator_live "
            "WHERE psych_state IN ('FOMO_EUPHORIA', 'CAPITULATION')").fetchall()
    except sqlite3.OperationalError:
        return []
    out: list[dict] = []
    for sym, state, up, down, rsi, pvv in rows:
        atype = FOMO_WARNING if state == "FOMO_EUPHORIA" else CAPITULATION_WATCH
        m = {"consecutive_up_days": up, "consecutive_down_days": down, "rsi_5m": rsi,
             "price_vs_vwap": pvv, "resistance": _level(conn, sym, "r1"),
             "delivery_trend": _delivery_trend(conn, sym)}
        out.append({"symbol": sym, "type": atype, "state": state,
                    "message": _builder(atype)(sym, m)})
    return out


def _level(conn, sym, col):
    try:
        r = conn.execute(
            f"SELECT {col} FROM indicator_levels WHERE symbol=? ORDER BY session_date DESC LIMIT 1",
            (sym,)).fetchone()
        return r[0] if r else None
    except sqlite3.OperationalError:
        return None


def _delivery_trend(conn, sym):
    try:
        r = conn.execute(
            "SELECT delivery_trend FROM delivery_conviction WHERE symbol=? "
            "ORDER BY session_date DESC LIMIT 1", (sym,)).fetchone()
        return r[0] if r else None
    except sqlite3.OperationalError:
        return None


def run_exhaustion_pass(conn, *, redis_client=None, now: _dt.datetime | None = None) -> dict:
    """Find exhaustion alerts, drop any within the 2h cooldown, log the rest."""
    now = now or now_ist()
    alerts = find_exhaustion(conn, now=now)
    fresh = [a for a in alerts if _claim(redis_client, a["symbol"], a["type"], now)]
    for a in fresh:
        log.info("exhaustion_alert", symbol=a["symbol"], type=a["type"])
    return {"found": len(alerts), "fired": len(fresh),
            "alerts": [{"symbol": a["symbol"], "type": a["type"]} for a in fresh]}


def _claim(redis_client, symbol: str, atype: str, now: _dt.datetime) -> bool:
    """True if not alerted in the last 2h (best-effort via Redis; fails open without it)."""
    if redis_client is None:
        return True
    key = f"psychdedup:{symbol}:{atype}"
    try:
        if redis_client.set(key, int(now.timestamp()), nx=True, ex=_COOLDOWN_SECS):
            return True
        return False
    except Exception:  # noqa: BLE001
        return True


def register_exhaustion_detector_job(scheduler, db_path: str) -> str:
    """Every minute during market hours (20.6); cheap read of indicator_live, dedup-gated."""
    from apscheduler.triggers.interval import IntervalTrigger

    from ..storage.db import open_db
    from .state_classifier import _connect_redis

    def _tick():
        if not is_market_open():
            return
        conn = open_db(db_path)
        try:
            log.info("exhaustion_tick", **run_exhaustion_pass(conn, redis_client=_connect_redis()))
        except Exception:
            log.exception("exhaustion_tick_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(seconds=60),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True)
    return JOB_ID
