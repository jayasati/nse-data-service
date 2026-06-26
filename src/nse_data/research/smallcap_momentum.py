"""Small-cap EOD momentum track (Task 3) — DELIBERATE, ISOLATED, paper-only.

RELAXO/PIXTRANS-type names (the fattest 25-Jun moves) are NOT in tradeable_universe and have NO
intraday candle data, so the spec's intraday detector would read nothing. Reshaped to EOD on
bhavcopy (which DOES cover them) — also the validated swing horizon.

ISOLATION (non-negotiable): this module is never imported by the conviction engine. Its paper
trades live in paper_book tagged strategy='smallcap_momentum' (own sizing, own track record);
the main run_paper_trade harness never touches them (not in its STRATEGIES). Signals are
audited in smallcap_signals; missed large movers are logged in universe_gaps.
"""
from __future__ import annotations

import pathlib as _pathlib
import time

import structlog

from ..costs.model import compute_costs

log = structlog.get_logger(__name__)
STRATEGY = "smallcap_momentum"
GAP_MOVE_PCT = 7.0       # |day move| >= this and out of every universe → log as a gap
MOM_PCT = 5.0            # entry: day move >= 5%
VOL_RATIO_MIN = 3.0      # entry: day volume >= 3x the 20-session average


def load_config() -> dict:
    import yaml
    path = _pathlib.Path(__file__).resolve().parents[3] / "config" / "small_cap_momentum_universe.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def _latest_bhav_date(conn) -> str | None:
    r = conn.execute("SELECT MAX(date) FROM raw_bhavcopy_cm").fetchone()
    return r[0] if r else None


def populate_universe_gaps(conn, date: str) -> int:
    """Log stocks that moved >= GAP_MOVE_PCT on `date` but are in NEITHER tradeable_universe NOR
    the small-cap track — silent gaps become auditable. Sourced from BHAVCOPY (covers every stock,
    unlike intraday_move_events which excludes illiquid names like RELAXO/PIXTRANS)."""
    members = set(load_config().get("members", []))
    tradeable = {r[0] for r in conn.execute("SELECT symbol FROM tradeable_universe")}
    ts = int(time.time())
    n = 0
    for sym, prev_c, close, turnover in conn.execute(
            "SELECT symbol, prev_close, close, turnover_lacs FROM raw_bhavcopy_cm "
            "WHERE date=? AND series='EQ' AND prev_close > 0", (date,)):
        move = (close - prev_c) / prev_c * 100.0
        if abs(move) < GAP_MOVE_PCT or sym in tradeable or sym in members:
            continue
        # crude reason: thin turnover → illiquid, else unknown (a conscious "why not us" tag)
        reason = "illiquid" if (turnover is not None and turnover < 100) else "unknown"
        conn.execute(
            "INSERT OR REPLACE INTO universe_gaps (ts, gap_date, symbol, move_pct, reason_out) "
            "VALUES (?,?,?,?,?)", (ts, date, sym, round(move, 2), reason))
        n += 1
    conn.commit()
    return n


def _avg_vol_20(conn, symbol: str, date: str) -> float | None:
    rows = conn.execute(
        "SELECT volume FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' AND date < ? "
        "ORDER BY date DESC LIMIT 20", (symbol, date)).fetchall()
    vols = [r[0] for r in rows if r[0]]
    return sum(vols) / len(vols) if vols else None


def _high_252(conn, symbol: str, date: str) -> float | None:
    r = conn.execute(
        "SELECT MAX(high) FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' AND date < ? "
        "AND date >= date(?, '-252 day')", (symbol, date, date)).fetchone()
    return r[0] if r and r[0] else None


def detect_signals(conn, date: str) -> list[dict]:
    """EOD momentum signals for the small-cap universe, from bhavcopy."""
    members = load_config().get("members", [])
    out = []
    for sym in members:
        row = conn.execute(
            "SELECT prev_close, close, volume, delivery_pct FROM raw_bhavcopy_cm "
            "WHERE symbol=? AND series='EQ' AND date=?", (sym, date)).fetchone()
        if not row or not row[0]:
            continue
        prev_c, close, vol, deliv = row
        move = (close - prev_c) / prev_c * 100.0
        avg20 = _avg_vol_20(conn, sym, date)
        vol_ratio = (vol / avg20) if (avg20 and vol) else None
        h252 = _high_252(conn, sym, date)
        is_brk = 1 if (h252 and close >= h252) else 0
        triggers = []
        if move >= MOM_PCT:
            triggers.append("momentum")
        if vol_ratio and vol_ratio >= VOL_RATIO_MIN:
            triggers.append("vol_surge")
        if is_brk:
            triggers.append("52w_breakout")
        sig = {"symbol": sym, "close": close, "move_pct": round(move, 2),
               "vol_ratio": round(vol_ratio, 2) if vol_ratio else None,
               "is_52w_breakout": is_brk, "delivery_pct": deliv,
               "signal": "+".join(triggers) if triggers else None}
        conn.execute(
            "INSERT OR REPLACE INTO smallcap_signals (signal_date, symbol, close, move_pct, "
            "vol_ratio, is_52w_breakout, delivery_pct, signal) VALUES (?,?,?,?,?,?,?,?)",
            (date, sym, close, sig["move_pct"], sig["vol_ratio"], is_brk, deliv, sig["signal"]))
        out.append(sig)
    conn.commit()
    return out


def _qualifies(sig: dict) -> bool:
    """Entry gate: a real momentum thrust on participation (or a clean 52w breakout on volume)."""
    if not sig["signal"]:
        return False
    has_vol = sig["vol_ratio"] is not None and sig["vol_ratio"] >= VOL_RATIO_MIN
    return ((sig["move_pct"] >= MOM_PCT and has_vol) or (sig["is_52w_breakout"] and has_vol))


def paper_trade(conn, date: str, signals: list[dict]) -> dict:
    """Open/close the ISOLATED small-cap paper book (strategy=smallcap_momentum). 0.5%/name
    sizing, 3% hard stop, 5-day max hold. Net-of-cost via the delivery cost model."""
    cfg = load_config().get("sizing", {})
    capital = cfg.get("capital_base_rs", 1_000_000)
    pos_pct = cfg.get("max_position_pct", 0.5) / 100.0
    stop_pct = cfg.get("hard_stop_pct", 3.0) / 100.0
    max_hold = cfg.get("max_hold_days", 5)
    now = int(time.time())
    opened = closed = 0

    # 1) manage exits on open small-cap positions using today's bhavcopy
    for pid, sym, entry_date, entry_px, stop_px, qty in conn.execute(
            "SELECT id, symbol, entry_date, entry_px, stop_px, qty FROM paper_book "
            "WHERE status='open' AND strategy=?", (STRATEGY,)):
        bar = conn.execute("SELECT low, close FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' "
                           "AND date=?", (sym, date)).fetchone()
        if not bar:
            continue
        low, close = bar
        held = conn.execute("SELECT julianday(?) - julianday(?)", (date, entry_date)).fetchone()[0]
        exit_px = reason = None
        if stop_px and low is not None and low <= stop_px:
            exit_px, reason = stop_px, "stop"
        elif held >= max_hold:
            exit_px, reason = close, "max_hold"
        if exit_px and qty and entry_px:
            tc = compute_costs(entry_px, exit_px, int(qty), "long", "delivery")
            net_pct = round(tc.net_pnl / (entry_px * qty) * 100, 2)
            conn.execute(
                "UPDATE paper_book SET status='closed', exit_date=?, exit_px=?, exit_reason=?, "
                "net_pct=?, net_pnl=?, updated_at=? WHERE id=?",
                (date, exit_px, reason, net_pct, round(tc.net_pnl, 2), now, pid))
            closed += 1

    # 2) open new entries on qualifying signals (skip if already holding the name)
    held_syms = {r[0] for r in conn.execute(
        "SELECT symbol FROM paper_book WHERE status='open' AND strategy=?", (STRATEGY,))}
    for sig in signals:
        if sig["symbol"] in held_syms or not _qualifies(sig):
            continue
        px = sig["close"]
        qty = int(capital * pos_pct / px)
        if qty < 1:
            continue
        stop_px = round(px * (1 - stop_pct), 2)
        conn.execute(
            "INSERT INTO paper_book (symbol, entry_date, entry_px, status, strategy, stop_px, qty, "
            "risk_rupees, direction, updated_at) VALUES (?,?,?,'open',?,?,?,?,'long',?)",
            (sig["symbol"], date, px, STRATEGY, stop_px, qty, round(qty * px * stop_pct, 2), now))
        opened += 1
    conn.commit()
    return {"opened": opened, "closed": closed}


def run_smallcap_pass(conn, *, date: str | None = None) -> dict:
    date = date or _latest_bhav_date(conn)
    if not date:
        return {"error": "no bhavcopy"}
    gaps = populate_universe_gaps(conn, date)
    signals = detect_signals(conn, date)
    book = paper_trade(conn, date, signals)
    report = {"date": date, "universe_gaps": gaps,
              "signals": sum(1 for s in signals if s["signal"]),
              "qualifying": sum(1 for s in signals if _qualifies(s)), **book}
    log.info("smallcap_pass", **report)
    return report


def register_smallcap_job(scheduler, db_path: str) -> str:
    """Nightly 19:35 IST (after the 19:30 bhavcopy/candle cron): isolated small-cap EOD pass."""
    from apscheduler.triggers.cron import CronTrigger

    from ..events.calendar import _feature_enabled
    from ..scheduler import market_hours
    from ..storage.db import open_db
    job_id = "smallcap_momentum"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        if not _feature_enabled("smallcap_momentum", True):
            return
        conn = open_db(db_path)
        try:
            run_smallcap_pass(conn)
        except Exception:
            log.exception("smallcap_pass_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=19, minute=35, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True)
    return job_id
