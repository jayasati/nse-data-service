"""Forward-validation paper tracks for the deal-flow (P2) and promoter (P3) signals.

These signals are INPUTS, not auto-scored into conviction (validation discipline). To find out
whether they actually have edge, each fresh buy-signal opens an isolated paper position here; we
hold for the signal's swing horizon (or a hard stop) and book net-of-cost P&L. After a quarter of
forward samples, the track record decides whether they earn a conviction weight.

Two strategies, both LONG-only v1 (the accumulation thesis), tagged in paper_book:
  deal_flow      — INSTITUTIONAL_BUY / _LARGE bulk-block buys, ~14-day hold
  promoter_flow  — PROMOTER_BUY / _STRONG / _SUSTAINED, ~30-day hold

Entry recency uses signal.created_at (uniform ISO) so it works regardless of the two tables'
different native date formats. EOD entries at bhavcopy close, restricted to the tradeable universe
for realism. Self-managed exits (stop or max-hold). NOT combined with the F&O conviction engine.
"""
from __future__ import annotations

import sqlite3
import time

import structlog

from ..costs.model import compute_costs

log = structlog.get_logger(__name__)

CAPITAL = 1_000_000
POS_PCT = 0.01        # 1% of capital per name
STOP_PCT = 5.0        # 5% hard stop (swing)

STRATEGIES = {
    "deal_flow": dict(
        table="large_deal_signals",
        buys=("INSTITUTIONAL_BUY", "INSTITUTIONAL_BUY_LARGE"),
        max_hold_days=14),
    "promoter_flow": dict(
        table="promoter_signals",
        buys=("PROMOTER_BUY", "PROMOTER_BUY_STRONG", "PROMOTER_SUSTAINED"),
        max_hold_days=30),
}


def _latest_bhav_date(conn) -> str | None:
    r = conn.execute("SELECT MAX(date) FROM raw_bhavcopy_cm").fetchone()
    return r[0] if r else None


def _bhav(conn, symbol: str, date: str):
    return conn.execute("SELECT low, close FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' "
                        "AND date=?", (symbol, date)).fetchone()


def _fresh_buy_symbols(conn, cfg: dict) -> list[str]:
    """Symbols with a buy-type signal recorded in the last 2 days (created_at is uniform ISO)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (cfg["table"],)).fetchone():
        return []
    ph = ",".join("?" * len(cfg["buys"]))
    return [r[0] for r in conn.execute(
        f"SELECT DISTINCT symbol FROM {cfg['table']} WHERE signal_type IN ({ph}) "
        "AND created_at >= datetime('now','-2 day')", cfg["buys"])]


def _run_strategy(conn, strat: str, cfg: dict, date: str, tradeable: set[str]) -> dict:
    now = int(time.time())
    opened = closed = 0
    # 1) exits — stop or max-hold
    for pid, sym, entry_date, entry_px, stop_px, qty in conn.execute(
            "SELECT id, symbol, entry_date, entry_px, stop_px, qty FROM paper_book "
            "WHERE status='open' AND strategy=?", (strat,)):
        bar = _bhav(conn, sym, date)
        if not bar:
            continue
        low, close = bar
        held = conn.execute("SELECT julianday(?)-julianday(?)", (date, entry_date)).fetchone()[0]
        exit_px = reason = None
        if stop_px and low is not None and low <= stop_px:
            exit_px, reason = stop_px, "stop"
        elif held >= cfg["max_hold_days"]:
            exit_px, reason = close, "max_hold"
        if exit_px and qty and entry_px:
            tc = compute_costs(entry_px, exit_px, int(qty), "long", "delivery")
            conn.execute(
                "UPDATE paper_book SET status='closed', exit_date=?, exit_px=?, exit_reason=?, "
                "net_pct=?, net_pnl=?, updated_at=? WHERE id=?",
                (date, exit_px, reason, round(tc.net_pnl / (entry_px * qty) * 100, 2),
                 round(tc.net_pnl, 2), now, pid))
            closed += 1
    # 2) entries — fresh buy signals not already held, in the tradeable universe
    held_syms = {r[0] for r in conn.execute(
        "SELECT symbol FROM paper_book WHERE status='open' AND strategy=?", (strat,))}
    for sym in _fresh_buy_symbols(conn, cfg):
        if sym in held_syms or sym not in tradeable:
            continue
        bar = _bhav(conn, sym, date)
        if not bar or not bar[1]:
            continue
        px = bar[1]
        # min 1 share so high-priced names (DIXON ₹14k, MARUTI ₹12k) aren't silently dropped —
        # that would bias the forward track toward cheap stocks. We measure % return / R, so a
        # slightly-oversized 1-share position on a pricey name is fine for validation.
        qty = max(1, int(CAPITAL * POS_PCT / px))
        stop_px = round(px * (1 - STOP_PCT / 100.0), 2)
        conn.execute(
            "INSERT INTO paper_book (symbol, entry_date, entry_px, status, strategy, stop_px, qty, "
            "risk_rupees, direction, updated_at) VALUES (?,?,?,'open',?,?,?,?,'long',?)",
            (sym, date, px, strat, stop_px, qty, round(qty * px * STOP_PCT / 100.0, 2), now))
        opened += 1
    conn.commit()
    return {"opened": opened, "closed": closed}


def run_pass(conn: sqlite3.Connection, *, date: str | None = None) -> dict:
    date = date or _latest_bhav_date(conn)
    if not date:
        return {"error": "no bhavcopy"}
    tradeable = {r[0] for r in conn.execute("SELECT symbol FROM tradeable_universe")}
    report: dict = {"date": date}
    for strat, cfg in STRATEGIES.items():
        report[strat] = _run_strategy(conn, strat, cfg, date, tradeable)
    log.info("signal_paper", **report)
    return report


def register_signal_paper_job(scheduler, db_path: str) -> str:
    """Nightly 22:30 IST — after the deal (16:30) + promoter (22:15) signal passes. Toggle gated."""
    from apscheduler.triggers.cron import CronTrigger

    from ..events.calendar import _feature_enabled
    from ..scheduler import market_hours
    from ..storage.db import open_db
    job_id = "signal_paper"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        if not _feature_enabled("signal_paper", True):
            return
        conn = open_db(db_path)
        try:
            run_pass(conn)
        except Exception:
            log.exception("signal_paper_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=22, minute=30, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True)
    return job_id
