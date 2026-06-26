"""FPI flow-regime signal over raw_nsdl_fpi_daily (custody-side daily FPI net flow).

MARKET-LEVEL, not sector: NSDL's daily report breaks down by asset_class × investment_route
(Equity/Debt × Stock-Exchange/Primary), with NO sector dimension. Foreign net flow is one of the
biggest drivers of Indian equity direction, so the 5-session cumulative equity net is a clean
risk-on/risk-off regime input for the swing book + the desk note. (True per-sector FPI rotation
would need NSDL's separate monthly sector-AUC report — a new collector.)

Surfaced in the morning brief + desk note. NOT auto-scored into conviction (validation discipline).
"""
from __future__ import annotations

import sqlite3

import structlog

log = structlog.get_logger(__name__)

# Thresholds on the 5-session cumulative equity FPI net (₹ cr). Daily flow is ~±2-9k cr, so a 5d
# cumulative beyond ±7.5k is a clear lean, beyond ±20k is a heavy risk-on/off regime.
STRONG_CR = 20_000.0
MOD_CR = 7_500.0


def _equity_net_series(conn: sqlite3.Connection, n: int = 5) -> list[tuple[str, float]]:
    return [(r[0], r[1]) for r in conn.execute(
        "SELECT as_of_date, net_cr FROM raw_nsdl_fpi_daily "
        "WHERE asset_class='Equity' AND investment_route='Stock Exchange' AND net_cr IS NOT NULL "
        "ORDER BY as_of_date DESC LIMIT ?", (n,))]


def classify(net_5d: float) -> str:
    if net_5d >= STRONG_CR:
        return "FPI_RISK_ON"
    if net_5d >= MOD_CR:
        return "FPI_BUYING"
    if net_5d <= -STRONG_CR:
        return "FPI_RISK_OFF"
    if net_5d <= -MOD_CR:
        return "FPI_SELLING"
    return "FPI_NEUTRAL"


def compute(conn: sqlite3.Connection) -> dict | None:
    s = _equity_net_series(conn, 5)
    if not s:
        return None
    net_1d = s[0][1]
    net_5d = round(sum(v for _, v in s), 1)
    return {"as_of_date": s[0][0], "net_1d_cr": round(net_1d, 1), "net_5d_cr": net_5d,
            "regime": classify(net_5d)}


def run_pass(conn: sqlite3.Connection) -> dict:
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='raw_nsdl_fpi_daily'").fetchone():
        return {"skipped": "no fpi feed"}
    out = compute(conn)
    if not out:
        return {"skipped": "no equity flow rows"}
    conn.execute(
        "INSERT OR REPLACE INTO fpi_flow (as_of_date, net_1d_cr, net_5d_cr, regime, created_at) "
        "VALUES (?,?,?,?,datetime('now'))",
        (out["as_of_date"], out["net_1d_cr"], out["net_5d_cr"], out["regime"]))
    conn.commit()
    log.info("fpi_flow", **out)
    return out


def register_fpi_flow_job(scheduler, db_path: str) -> str:
    """Nightly 18:30 IST — after the NSDL FPI EOD collector lands. Trading-day + toggle gated."""
    from apscheduler.triggers.cron import CronTrigger

    from ..events.calendar import _feature_enabled
    from ..scheduler import market_hours
    from ..storage.db import open_db
    job_id = "fpi_flow"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        if not _feature_enabled("fpi_flow", True):
            return
        conn = open_db(db_path)
        try:
            run_pass(conn)
        except Exception:
            log.exception("fpi_flow_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=18, minute=30, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True)
    return job_id
