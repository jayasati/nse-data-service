"""Macro-theme / basket-rotation detector (Task 2).

On 25-Jun the day's alpha was CORRELATED baskets moving on one macro driver (auto-ancillary
on Nifty Auto +2.25%, metals on the dollar/LME crash), not 17 idiosyncratic stories — and we
had no machinery to see it. This detects, every 15 min, when a basket's cross-sectional breadth
aligns with its macro driver strongly enough to call a regime, and flags members for SWING
(1-5 day) positioning.

Integration is deliberately conservative: a strong signal PROMOTES members to the live watchlist
(reason 'basket_rotation') and supplies a cause-attribution label — it does NOT mutate the
validation-gated conviction_daily score (injecting an unvalidated +score into the forward paper
track would violate the project's validation discipline). Forward-validate via a paper strategy
first, then wire a score weight.
"""
from __future__ import annotations

import pathlib as _pathlib
import sqlite3
import time

import structlog

log = structlog.get_logger(__name__)

ADV_THRESH = 0.5      # a member is "advancing" if it's up > +0.5% (declining if < -0.5%)
BREADTH_MIN = 0.5     # majority of the basket must be moving one way
DRIVER_MIN = 1.0      # the macro driver must have moved >= 1%
PROMOTE_CONF = 0.8    # confidence at/above which members are promoted to the watchlist


def load_baskets() -> dict:
    import yaml
    path = _pathlib.Path(__file__).resolve().parents[3] / "config" / "sector_baskets.yaml"
    if not path.exists():
        return {}
    return (yaml.safe_load(path.read_text()) or {}).get("baskets", {})


def compute_basket_signal(name: str, cfg: dict, member_returns: dict[str, float],
                          driver_pct: float | None) -> dict | None:
    """PURE core (no DB) — given each member's % return and the driver's % move, decide whether
    the basket is rotating. Returns a signal dict or None. Testable in isolation."""
    rets = {s: r for s, r in member_returns.items() if r is not None}
    n = len(rets)
    if n == 0 or driver_pct is None:
        return None
    adv = sum(1 for r in rets.values() if r > ADV_THRESH)
    dec = sum(1 for r in rets.values() if r < -ADV_THRESH)
    breadth = (adv - dec) / n
    direction = cfg.get("direction", "co-directional")

    # Is the breadth aligned with the driver in the configured sense?
    if direction == "inverse":
        # members move AGAINST the driver: long basket when driver is down, short when up
        aligned = (breadth > 0 and driver_pct <= -DRIVER_MIN) or \
                  (breadth < 0 and driver_pct >= DRIVER_MIN)
    else:  # co-directional
        aligned = (breadth > 0 and driver_pct >= DRIVER_MIN) or \
                  (breadth < 0 and driver_pct <= -DRIVER_MIN)

    if abs(breadth) < BREADTH_MIN or abs(driver_pct) < DRIVER_MIN or not aligned:
        return None

    return {
        "basket_name": name,
        "breadth_score": round(breadth, 3),
        "driver_name": cfg.get("driver_index") or cfg.get("driver_macro"),
        "driver_move_pct": round(driver_pct, 2),
        "signal_type": "BASKET_LONG" if breadth > 0 else "BASKET_SHORT",
        "member_count": n,
        "advancing": adv,
        "declining": dec,
        "confidence": round(min(1.0, abs(breadth) * abs(driver_pct) / 2), 3),
    }


# ── live data adapters ────────────────────────────────────────────────────────────────
def _driver_pct(conn: sqlite3.Connection, cfg: dict) -> float | None:
    """Driver % move. Sector index → intraday pct_change (raw_indices). Macro (brent/usdinr)
    → day-over-day from raw_macro_market (DAILY only — coarser, fires less often)."""
    idx = cfg.get("driver_index")
    if idx:
        r = conn.execute("SELECT pct_change FROM raw_indices WHERE index_name=? "
                         "ORDER BY as_of DESC LIMIT 1", (idx,)).fetchone()
        return float(r[0]) if r and r[0] is not None else None
    col = cfg.get("driver_macro")
    if col in ("brent", "usdinr"):
        rows = conn.execute(f"SELECT {col} FROM raw_macro_market WHERE {col} IS NOT NULL "
                            "ORDER BY date DESC LIMIT 2").fetchall()
        if len(rows) == 2 and rows[1][0]:
            return (rows[0][0] - rows[1][0]) / rows[1][0] * 100.0
    return None


def _member_returns_live(conn: sqlite3.Connection, members: list[str]) -> dict[str, float]:
    """Each member's intraday % vs prior close: indicator_live.ltp vs latest bhavcopy close."""
    out: dict[str, float] = {}
    for sym in members:
        lt = conn.execute("SELECT ltp FROM indicator_live WHERE symbol=?", (sym,)).fetchone()
        pc = conn.execute("SELECT close FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' "
                          "ORDER BY date DESC LIMIT 1", (sym,)).fetchone()
        if lt and lt[0] and pc and pc[0]:
            out[sym] = (lt[0] - pc[0]) / pc[0] * 100.0
    return out


def run_basket_pass(conn: sqlite3.Connection, *, now=None) -> dict:
    from ..scheduler import market_hours
    from ..signals.watchlist import add_to_watchlist
    now = now or market_hours.now_ist()
    today = now.date().isoformat()
    ts = int(time.time())
    baskets = load_baskets()
    fired, promoted = [], 0
    for name, cfg in baskets.items():
        sig = compute_basket_signal(name, cfg, _member_returns_live(conn, cfg.get("members", [])),
                                    _driver_pct(conn, cfg))
        if not sig:
            continue
        # one signal per basket per day (PK collision = already active → skip)
        cur = conn.execute(
            "INSERT OR IGNORE INTO basket_signals (signal_date, basket_name, ts, breadth_score, "
            "driver_name, driver_move_pct, signal_type, member_count, advancing, declining, "
            "confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (today, name, ts, sig["breadth_score"], sig["driver_name"], sig["driver_move_pct"],
             sig["signal_type"], sig["member_count"], sig["advancing"], sig["declining"],
             sig["confidence"]))
        if cur.rowcount == 0:
            continue                       # already fired today
        fired.append(f"{name}:{sig['signal_type']}({sig['confidence']})")
        if sig["confidence"] >= PROMOTE_CONF:
            now_iso = now.isoformat()
            exp_iso = (now.date() + __import__("datetime").timedelta(days=5)).isoformat()
            for m in cfg.get("members", []):
                add_to_watchlist(conn, m, f"basket_rotation:{name}", now_iso, exp_iso)
                promoted += 1
    conn.commit()
    report = {"baskets": len(baskets), "fired": fired, "promoted": promoted}
    log.info("basket_pass", **report)
    return report


def active_basket_for(conn: sqlite3.Connection, symbol: str, date: str) -> dict | None:
    """For cause attribution: if `symbol` is a member of a basket that fired on `date`, return
    that basket's signal — so the move is attributed to the theme, not 'unknown'."""
    for name, cfg in load_baskets().items():
        if symbol in cfg.get("members", []):
            r = conn.execute("SELECT signal_type, confidence, driver_name, driver_move_pct "
                             "FROM basket_signals WHERE signal_date=? AND basket_name=?",
                             (date, name)).fetchone()
            if r:
                return {"basket": name, "signal_type": r[0], "confidence": r[1],
                        "driver": r[2], "driver_move_pct": r[3]}
    return None


def register_basket_job(scheduler, db_path: str) -> str:
    """Every 15 min, 09:30–15:30 IST: detect basket rotations. Trading-day + toggle gated."""
    from apscheduler.triggers.cron import CronTrigger

    from ..events.calendar import _feature_enabled
    from ..scheduler import market_hours
    from ..storage.db import open_db
    job_id = "basket_rotation"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        if not _feature_enabled("basket_rotation", True):
            return
        conn = open_db(db_path)
        try:
            run_basket_pass(conn)
        except Exception:
            log.exception("basket_pass_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour="9-15", minute="*/15", timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True)
    return job_id
