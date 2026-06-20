"""Options analytics pass + scheduling (FEATURE_CHECKLIST Week 23, tasks 23.3–23.7).

For each symbol with a fresh option chain, compute GEX / max-pain / PCR over the nearest
expiry and persist to `options_metrics`; mirror the index (NIFTY) GEX onto `market_state`
(23.3) where the morning brief + confidence layer read it. The PCR-extreme and
max-pain-drift signals (23.6/23.7) are surfaced in the report — they feed the gated
dispatcher like every other signal.

`run_options_pass` is the unit; `register_options_job` runs it every 5 min, market hours.
"""
from __future__ import annotations

import sqlite3
import time

import structlog

from ..scheduler.market_hours import is_market_open
from . import greeks as gk

log = structlog.get_logger()
JOB_ID = "options_metrics"
INDEX_SYMBOLS = ("NIFTY", "BANKNIFTY", "FINNIFTY")


def _latest_as_of(conn, symbol: str) -> int | None:
    r = conn.execute("SELECT MAX(as_of) FROM raw_option_chain WHERE symbol=?", (symbol,)).fetchone()
    return r[0] if r and r[0] else None


def _nearest_expiry(conn, symbol: str, as_of: int) -> str | None:
    """The soonest non-expired expiry in the snapshot (most front-month liquidity)."""
    expiries = [r[0] for r in conn.execute(
        "SELECT DISTINCT expiry FROM raw_option_chain WHERE symbol=? AND as_of=?", (symbol, as_of))]
    dated = [(gk.days_to_expiry(e, as_of), e) for e in expiries]
    dated = [(d, e) for d, e in dated if d is not None]
    return min(dated)[1] if dated else None


def _chain_rows(conn, symbol: str, as_of: int, expiry: str) -> tuple[list[dict], float]:
    t_years = (gk.days_to_expiry(expiry, as_of) or 1) / 365.0
    rows, spot = [], 0.0
    for strike, ot, oi, iv, uv in conn.execute(
        "SELECT strike, option_type, open_interest, implied_volatility, underlying_value "
        "FROM raw_option_chain WHERE symbol=? AND as_of=? AND expiry=?", (symbol, as_of, expiry)):
        if uv and uv > spot:
            spot = uv                                       # some rows carry underlying_value=0
        rows.append({"strike": strike, "option_type": ot, "oi": oi,
                     "iv": (iv or 0) / 100.0, "t_years": t_years})
    return rows, spot


def compute_symbol(conn, symbol: str) -> dict | None:
    as_of = _latest_as_of(conn, symbol)
    if not as_of:
        return None
    expiry = _nearest_expiry(conn, symbol, as_of)
    if not expiry:
        return None
    rows, spot = _chain_rows(conn, symbol, as_of, expiry)
    if not rows:
        return None
    gex = gk.gamma_exposure(rows, spot) if spot > 0 else None
    mp = gk.max_pain(rows)
    pcr = gk.put_call_ratio(rows)
    return {"symbol": symbol, "expiry": expiry, "as_of": as_of, "spot": spot,
            "max_pain": mp, "pcr": pcr,
            "gex_total": (gex or {}).get("gex_total"), "gex_sign": (gex or {}).get("gex_sign"),
            "gex_flip_level": (gex or {}).get("gex_flip_level"),
            "pcr_signal": gk.pcr_signal(pcr), "max_pain_drift": gk.max_pain_drift(spot, mp)}


def run_options_pass(conn: sqlite3.Connection, *, symbols: list[str] | None = None) -> dict:
    if symbols is None:
        symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM raw_option_chain")]
    now = int(time.time())
    written = 0
    signals: list[dict] = []
    for sym in symbols:
        try:
            m = compute_symbol(conn, sym)
        except Exception:  # noqa: BLE001 — one bad symbol shouldn't kill the pass
            log.exception("options_compute_failed", symbol=sym)
            continue
        if not m:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO options_metrics (symbol, expiry, as_of, spot, max_pain, pcr, "
            "gex_total, gex_sign, gex_flip_level, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (m["symbol"], m["expiry"], m["as_of"], m["spot"], m["max_pain"], m["pcr"],
             m["gex_total"], m["gex_sign"], m["gex_flip_level"], now))
        written += 1
        if sym in INDEX_SYMBOLS:
            _mirror_index_gex(conn, m)
        if m["pcr_signal"]:
            signals.append({"symbol": sym, "type": m["pcr_signal"], "pcr": m["pcr"]})
        if m["max_pain_drift"]:
            signals.append({"symbol": sym, "type": "max_pain_drift", **m["max_pain_drift"]})
    conn.commit()
    return {"symbols": len(symbols), "written": written, "signals": signals}


def _mirror_index_gex(conn, m: dict) -> None:
    """Write the index GEX onto the latest market_state row (task 23.3)."""
    try:
        conn.execute(
            "UPDATE market_state SET gex_total=?, gex_sign=?, gex_flip_level=? "
            "WHERE as_of=(SELECT MAX(as_of) FROM market_state)",
            (m["gex_total"], m["gex_sign"], m["gex_flip_level"]))
    except sqlite3.OperationalError:
        pass


def register_options_job(scheduler, db_path: str) -> str:
    """Every 5 min during market hours, alongside the option-chain collector (task 23.2)."""
    from apscheduler.triggers.interval import IntervalTrigger

    from ..storage.db import open_db

    def _tick():
        if not is_market_open():
            return
        conn = open_db(db_path)
        try:
            rep = run_options_pass(conn)
            log.info("options_metrics", written=rep["written"], signals=len(rep["signals"]))
        except Exception:
            log.exception("options_metrics_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(seconds=300),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True)
    return JOB_ID
