"""Live mode — intraday scan → dedicated 'sweep' alert channel.

Reuses the SAME `scan_setups` engine as the backtest, on the latest candles. Every 5 min during
market hours it looks for setups whose ENTRY just triggered (within `recent_min`), dedups against
`daily_sweep_signals`, and dispatches a card to the `sweep` channel (its own ntfy topic, separate
from the proven alerts — this is a forward-test of an as-yet-unvalidated strategy).
"""
from __future__ import annotations

import time

import structlog

from ...bot.dispatcher import load_telegram_config, send_telegram
from .config import DEFAULT, DailySweepConfig
from .data import read_1h, read_5m, read_daily
from .setup import SweepSetup, scan_setups

log = structlog.get_logger()
JOB_ID = "daily_sweep_live"
_WATCH = ("NIFTY", "BANKNIFTY", "FINNIFTY", "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN",
          "INFY", "TCS", "TATAMOTORS", "BAJFINANCE", "LT", "AXISBANK", "MARUTI")


def build_alert(s: SweepSetup) -> str:
    risk_pct = abs(s.entry_price - s.stop) / s.entry_price * 100
    arrow = "🟢 LONG" if s.direction == "long" else "🔴 SHORT"
    return (
        f"🌊 Daily Sweep — {s.symbol} {arrow}\n"
        f"Daily trend: {s.daily_trend}\n"
        f"Sweep {s.sweep_time:%H:%M} (swept {s.swept_level:.1f}) → BOS {s.bos_time:%H:%M}\n"
        f"FVG: {s.fvg_low:.1f}–{s.fvg_high:.1f}\n"
        f"Entry {s.entry_price:.1f} | SL {s.stop:.1f} (−{risk_pct:.2f}%) | "
        f"Target {s.target:.1f} (1:{s.rr:.0f})\n"
        f"Qty {s.qty} · risk ₹{s.risk_rupees:.0f}\n"
        f"⚠ Forward-test signal — strategy not yet validated."
    )


def scan_live(conn, *, config: DailySweepConfig = DEFAULT, symbols=_WATCH, now=None,
              recent_min: float = 10.0, sender=send_telegram) -> list[str]:
    """Find just-triggered setups across the watchlist, dedup, alert. Returns fired fingerprints."""
    import pandas as pd
    now_ts = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="Asia/Kolkata")
    token, chat_id = load_telegram_config()
    fired: list[str] = []
    for sym in symbols:
        try:
            setups = scan_setups(read_daily(conn, sym), read_1h(conn, sym), read_5m(conn, sym),
                                 config=config, symbol=sym)
        except Exception:  # noqa: BLE001 — one symbol shouldn't kill the pass
            log.exception("daily_sweep_scan_failed", symbol=sym)
            continue
        for s in setups:
            if (now_ts - s.entry_time).total_seconds() / 60.0 > recent_min:
                continue                                   # only setups that just triggered
            fp = f"{sym}:{s.entry_time.isoformat()}:{s.direction}"
            cur = conn.execute(
                "INSERT OR IGNORE INTO daily_sweep_signals (fingerprint, symbol, direction, "
                "daily_trend, sweep_time, bos_time, fvg_low, fvg_high, entry_time, entry_price, "
                "stop, target, qty, rr, alerted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fp, sym, s.direction, s.daily_trend, s.sweep_time.isoformat(),
                 s.bos_time.isoformat(), s.fvg_low, s.fvg_high, s.entry_time.isoformat(),
                 s.entry_price, s.stop, s.target, s.qty, s.rr, int(time.time())))
            if cur.rowcount == 0:
                continue                                   # already alerted
            sender(token, chat_id, build_alert(s), channel="sweep")
            fired.append(fp)
    conn.commit()
    return fired


def register_daily_sweep_job(scheduler, db_path: str) -> str:
    """Every 5 min during market hours — scan + alert (entries self-gate to session windows)."""
    from apscheduler.triggers.interval import IntervalTrigger

    from ...scheduler.market_hours import is_market_open
    from ...storage.db import open_db

    def _tick():
        if not is_market_open():
            return
        conn = open_db(db_path)
        try:
            fired = scan_live(conn)
            if fired:
                log.info("daily_sweep_live", fired=len(fired))
        except Exception:
            log.exception("daily_sweep_live_failed")
        finally:
            conn.close()

    scheduler.add_job(_tick, trigger=IntervalTrigger(seconds=300), id=JOB_ID,
                      max_instances=1, coalesce=True, replace_existing=True)
    return JOB_ID
