"""P3 — promoter/insider signal classification over raw_insider_trading.

Turns raw SAST/PIT filings into typed swing signals (buy-strong / sustained / pledge). SWING-ONLY
(horizon 10-30d, constraint #8) — never an intraday trigger. Writes its own table as an INPUT the
conviction engine MAY read; conviction_add is a SUGGESTION, not auto-applied (validation discipline).

NOTE: raw_insider_trading is currently empty on the box (the NSE corporates-pit feed isn't
populating — likely Akamai-blocked). This layer is correct and ready; it stays dark until that
upstream feed is fixed. No fabrication — empty input → empty output.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

import structlog

log = structlog.get_logger(__name__)

PROMOTER_CATS = ("promoter",)            # matches 'Promoters', 'Promoter Group', 'Promoter & ...'
KMP_CATS = ("key managerial", "kmp", "director", "designated person")
SKIP_MODES = ("inter-se", "gift", "transmission", "off market inter")

STRONG_BUY_PCT = 0.5
NORMAL_BUY_PCT = 0.1
SELL_ALERT_PCT = -0.5
SUSTAINED_CUM_PCT = 1.0


def _acquirer_type(category: str | None) -> str:
    c = (category or "").lower()
    if any(k in c for k in PROMOTER_CATS):
        return "PROMOTER_GROUP" if "group" in c else "PROMOTER"
    if any(k in c for k in KMP_CATS):
        return "DIRECTOR" if "director" in c else "KMP"
    return "OTHER"


def _txn_kind(transaction_type: str | None, mode: str | None) -> str:
    t = (transaction_type or "").lower()
    m = (mode or "").lower()
    if any(k in t + " " + m for k in ("pledge", "encumbr")):
        if any(k in t + " " + m for k in ("revoc", "release", "invocation")):
            return "PLEDGE_RELEASE"
        return "PLEDGE"
    if any(k in t for k in ("buy", "purchase", "acqui")):
        return "BUY"
    if any(k in t for k in ("sell", "sale", "dispos")):
        return "SELL"
    return "OTHER"


# NSE's befAcqSharesPer/afterAcqSharesPer are not cleanly "% of capital held" for every filing
# type (pledge/encumbrance rows, multi-class securities) → some implausible single-filing deltas
# (e.g. NTPC −71%). Guard the BUY/SELL holding-% path against obvious artifacts pending a proper
# semantics calibration. Pledge signals don't use this magnitude.
SANITY_MAX_CHANGE_PCT = 25.0


def classify(acquirer_type: str, txn_kind: str, holding_change_pct: float,
             cumulative_buy_30d: float = 0.0) -> dict:
    """Promoter-grade signal. Non-promoter/KMP and tiny moves → NEUTRAL."""
    promoterish = acquirer_type in ("PROMOTER", "PROMOTER_GROUP")
    if txn_kind in ("BUY", "SELL") and abs(holding_change_pct) > SANITY_MAX_CHANGE_PCT:
        return dict(signal_type="NEUTRAL", signal_strength=0.0, horizon_days=None,
                    conviction_add=0)  # implausible single-filing % → data artifact, don't trust
    if txn_kind == "BUY" and promoterish:
        if cumulative_buy_30d >= SUSTAINED_CUM_PCT:
            return dict(signal_type="PROMOTER_SUSTAINED", signal_strength=0.95,
                        horizon_days=30, conviction_add=25)
        if holding_change_pct >= STRONG_BUY_PCT:
            return dict(signal_type="PROMOTER_BUY_STRONG", signal_strength=0.85,
                        horizon_days=20, conviction_add=20)
        if holding_change_pct >= NORMAL_BUY_PCT:
            return dict(signal_type="PROMOTER_BUY", signal_strength=0.65,
                        horizon_days=15, conviction_add=12)
    if txn_kind == "SELL" and promoterish and holding_change_pct <= SELL_ALERT_PCT:
        return dict(signal_type="PROMOTER_SELL_ALERT", signal_strength=0.7,
                    horizon_days=None, conviction_add=-15)
    if txn_kind == "PLEDGE" and promoterish:
        return dict(signal_type="PLEDGE_INCREASE", signal_strength=0.6,
                    horizon_days=None, conviction_add=-15)
    if txn_kind == "PLEDGE_RELEASE" and promoterish:
        return dict(signal_type="PLEDGE_DECREASE", signal_strength=0.5,
                    horizon_days=None, conviction_add=10)
    return dict(signal_type="NEUTRAL", signal_strength=0.0, horizon_days=None, conviction_add=0)


def _cumulative_buy_30d(conn, symbol: str, upto: str) -> float:
    """Sum of promoter BUY holding-change% for the symbol over the trailing 30 days."""
    since = (_dt.date.fromisoformat(upto[:10]) - _dt.timedelta(days=30)).isoformat()
    rows = conn.execute(
        "SELECT acquirer_category, transaction_type, mode_of_acquisition, "
        "holding_before, holding_after FROM raw_insider_trading "
        "WHERE symbol=? AND intimation_date>=? AND intimation_date<=?", (symbol, since, upto)).fetchall()
    total = 0.0
    for cat, txn, mode, hb, ha in rows:
        if _acquirer_type(cat) in ("PROMOTER", "PROMOTER_GROUP") and _txn_kind(txn, mode) == "BUY":
            if hb is not None and ha is not None:
                total += max(0.0, ha - hb)
    return round(total, 4)


def run_pass(conn: sqlite3.Connection, date: str | None = None) -> dict:
    """Classify filings whose intimation_date == `date` (default: latest)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='raw_insider_trading'").fetchone():
        return {"filings": 0, "signals": 0, "note": "raw_insider_trading missing"}
    if date is None:
        r = conn.execute("SELECT MAX(intimation_date) FROM raw_insider_trading").fetchone()
        date = r[0] if r else None
    if not date:
        return {"filings": 0, "signals": 0, "note": "no insider data (feed empty)"}
    rows = conn.execute(
        "SELECT symbol, acquirer_name, acquirer_category, transaction_type, mode_of_acquisition, "
        "holding_before, holding_after FROM raw_insider_trading WHERE intimation_date=?",
        (date,)).fetchall()
    signals = 0
    for sym, acq, cat, txn, mode, hb, ha in rows:
        if (mode or "").lower() in SKIP_MODES:
            continue
        atype = _acquirer_type(cat)
        tkind = _txn_kind(txn, mode)
        change = round((ha - hb), 4) if (hb is not None and ha is not None) else 0.0
        cum = _cumulative_buy_30d(conn, sym, date) if tkind == "BUY" else 0.0
        sig = classify(atype, tkind, change, cum)
        conn.execute(
            "INSERT OR REPLACE INTO promoter_signals (symbol, filing_date, acquirer_name, "
            "acquirer_type, txn_type, holding_change_pct, cumulative_buy_30d, signal_type, "
            "signal_strength, horizon_days, conviction_add, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (sym, date, acq or "?", atype, tkind, change, cum, sig["signal_type"],
             sig["signal_strength"], sig["horizon_days"], sig["conviction_add"]))
        if sig["signal_type"] not in ("NEUTRAL",):
            signals += 1
    conn.commit()
    report = {"date": date, "filings": len(rows), "signals": signals}
    log.info("promoter_signals", **report)
    return report


def register_promoter_signals_job(scheduler, db_path: str) -> str:
    """Nightly 22:15 IST (after the insider-trading collector). Trading-day + toggle gated."""
    from apscheduler.triggers.cron import CronTrigger

    from ..events.calendar import _feature_enabled
    from ..scheduler import market_hours
    from ..storage.db import open_db
    job_id = "promoter_signals"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        if not _feature_enabled("promoter_signals", True):
            return
        conn = open_db(db_path)
        try:
            run_pass(conn)
        except Exception:
            log.exception("promoter_signals_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=22, minute=15, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True)
    return job_id
