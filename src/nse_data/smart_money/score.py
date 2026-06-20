"""Smart-money composite score (FEATURE_CHECKLIST Week 22, tasks 22.5/22.6).

A daily 0-1 read of where institutional money is leaning, weighted over the components we
can actually measure (graceful — missing feeds drop out and the rest re-normalise):

  FII cash flow        raw_fii_dii (FII/FPI net)            weight 25%
  FII derivatives      raw_participant_oi (FII positioning) weight 20%
  DII activity         raw_fii_dii (DII net)                weight 15%
  Block-deal tier      raw_large_deals × institution tiers  weight 15%
  (promoter/SLB legs are spec'd but their feeds are empty/absent → dropped for now)

> 0.70 = accumulation, < 0.40 = distribution. Written to smart_money_daily; the confidence
scorer (Layer 8, 22.7) and alert line (22.8) read it — gated dispatch like every signal.

Pure scorers are unit-tested; the pass glues them to SQLite.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path

import structlog

log = structlog.get_logger()
JOB_ID = "smart_money"

_CASH_THRESHOLD = 500.0          # ₹cr net flow that counts as a clear lean
_WEIGHTS = {"fii_cash": 0.25, "fii_deriv": 0.20, "dii": 0.15, "block_tier": 0.15}


# ---- pure component scorers (0-1) ------------------------------------------

def flow_score(net_cr: float | None, *, threshold: float = _CASH_THRESHOLD, hi=0.7, lo=0.3) -> float | None:
    if net_cr is None:
        return None
    if net_cr > threshold:
        return hi
    if net_cr < -threshold:
        return lo
    return 0.5


def fii_deriv_score(fii_row: dict | None) -> float | None:
    """FII derivative lean: net future-index + index call-vs-put longs. 0.3 (bearish)…0.7 (bullish)."""
    if not fii_row:
        return None
    fut_net = (fii_row.get("fut_idx_long") or 0) - (fii_row.get("fut_idx_short") or 0)
    opt_net = (fii_row.get("opt_idx_call_long") or 0) - (fii_row.get("opt_idx_put_long") or 0)
    bull = (1 if fut_net > 0 else -1) + (1 if opt_net > 0 else -1)        # -2..+2
    return round(0.5 + bull * 0.1, 2)


def block_tier_score(net_tier_value_cr: float | None) -> float | None:
    """Tier-1/2 institution net block buying (+) vs selling (−). None when no tiered deals."""
    if net_tier_value_cr is None:
        return None
    if net_tier_value_cr > 0:
        return 0.75
    if net_tier_value_cr < 0:
        return 0.35
    return 0.5


def composite(components: dict) -> float | None:
    """Weighted mean over present components, re-normalised (graceful)."""
    num = den = 0.0
    for k, w in _WEIGHTS.items():
        v = components.get(k)
        if v is None:
            continue
        num += w * v
        den += w
    return round(num / den, 3) if den > 0 else None


# ---- institution tiers + DB readers ----------------------------------------

def load_institutions() -> dict:
    import yaml
    p = Path(__file__).resolve().parents[3] / "config" / "institution_database.yaml"
    try:
        return yaml.safe_load(p.read_text()) or {}
    except (FileNotFoundError, Exception):  # noqa: BLE001
        return {}


def _net_flow(conn, category_like: str) -> float | None:
    try:
        r = conn.execute(
            "SELECT net_value FROM raw_fii_dii WHERE category LIKE ? ORDER BY fetched_at DESC LIMIT 1",
            (category_like,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None


def _fii_participant(conn) -> dict | None:
    try:
        cols = "fut_idx_long,fut_idx_short,opt_idx_call_long,opt_idx_put_long"
        r = conn.execute(
            f"SELECT {cols} FROM raw_participant_oi WHERE client_type='FII' "
            "ORDER BY report_date DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(zip(cols.split(","), r)) if r else None


def _tiered_block_net(conn, tiers: list[str]) -> float | None:
    """Net ₹cr of recent (90d) bulk/block deals whose client matches a tiered institution."""
    if not tiers:
        return None
    try:
        rows = conn.execute(
            "SELECT client_name, buy_sell, quantity, weighted_avg_price FROM raw_large_deals "
            "WHERE deal_type IN ('block','bulk') AND buy_sell IN ('BUY','SELL') "
            "AND client_name IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return None
    pats = [t.lower() for t in tiers]
    net = 0.0
    matched = False
    for client, bs, qty, watp in rows:
        if not any(p in (client or "").lower() for p in pats):
            continue
        if not (qty and watp):
            continue
        matched = True
        val = qty * watp / 1e7
        net += val if bs == "BUY" else -val
    return round(net, 1) if matched else None


def compute_smart_money(conn: sqlite3.Connection) -> dict:
    inst = load_institutions()
    tiers = (inst.get("tier1") or []) + (inst.get("tier2") or [])
    components = {
        "fii_cash": flow_score(_net_flow(conn, "FII%")),
        "fii_deriv": fii_deriv_score(_fii_participant(conn)),
        "dii": flow_score(_net_flow(conn, "DII%")),
        "block_tier": block_tier_score(_tiered_block_net(conn, tiers)),
    }
    return {"score": composite(components), **components}


def run_smart_money_pass(conn: sqlite3.Connection, *, now=None) -> dict:
    today = (now or date.today()).isoformat() if not isinstance(now, str) else now
    m = compute_smart_money(conn)
    present = [k for k in _WEIGHTS if m.get(k) is not None]
    conn.execute(
        "INSERT OR REPLACE INTO smart_money_daily (as_of_date, score, fii_cash, fii_deriv, dii, "
        "block_tier, components, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (today, m["score"], m["fii_cash"], m["fii_deriv"], m["dii"], m["block_tier"],
         ",".join(present) or None, int(time.time())))
    conn.commit()
    return {"date": today, "score": m["score"], "components": present}


def register_smart_money_job(scheduler, db_path: str) -> str:
    """Daily 20:00 IST (task 22.5) — after the FII/DII + participant-OI feeds land."""
    from apscheduler.triggers.cron import CronTrigger

    from ..scheduler import market_hours
    from ..storage.db import open_db

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        conn = open_db(db_path)
        try:
            log.info("smart_money", **run_smart_money_pass(conn, now=market_hours.now_ist().date()))
        except Exception:
            log.exception("smart_money_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=20, minute=0, timezone=market_hours.IST),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True)
    return JOB_ID
