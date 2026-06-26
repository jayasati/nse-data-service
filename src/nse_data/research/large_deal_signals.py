"""P2 — entity classification + signal layer for bulk/block deals.

raw_large_deals tells us a deal happened; this tells us WHO and whether it's institutional flow
worth noting (the 25-Jun blind spot: we saw moves but couldn't say "an FII bought ₹X cr"). This is
descriptive labelling of real disclosed flow — no validation gate needed — and it writes to its own
table as an INPUT the conviction engine MAY read (not an auto-score). SWING context, not intraday.
"""
from __future__ import annotations

import sqlite3

import structlog

log = structlog.get_logger(__name__)

INSURANCE_KW = ("insurance", "life insurance", " lic ", "lic ", "gic re", "assurance")
MF_KW = ("mutual fund", " mf ", "asset management", "amc", "investment trust",
         "templeton", "franklin", "nippon", "icici pru", "sbi mutual", "hdfc mutual",
         "axis mutual", "kotak mutual", "dsp ", "mirae", "uti ", "edelweiss mf",
         "growth opportunities fund", "emerging markets fund", "small cap fund")
FII_KW = ("mauritius", "singapore", "cayman", "luxembourg", "ireland", "offshore",
          "global", "international", "emerging market", "fii", "fpi", "foreign",
          "american funds", "vanguard", "blackrock", "morgan stanley", "government of singapore")
# corporate (non-individual, non-classified-institution) suffixes
CORP_KW = ("limited", " ltd", "llp", "private", "pvt", "securities", "capital", "finvest",
           "finwealth", "holdings", "ventures", "advisors", "consultancy", "enterprises",
           "trading", "agro", "industries", "corporation", "company")

INST_BUY_LARGE_CR = 10.0
INST_SELL_LARGE_CR = 50.0


def classify_entity(client_name: str | None) -> str:
    """Best-effort entity classification from the disclosed client name."""
    n = f" {(client_name or '').lower()} "
    if any(k in n for k in INSURANCE_KW):
        return "INSURANCE"
    if any(k in n for k in MF_KW):
        return "MF"
    if any(k in n for k in FII_KW):
        return "FII"
    if "promoter" in n:
        return "PROMOTER"
    if any(k in n for k in CORP_KW):
        return "CORPORATE"
    # no institutional/corporate markers → looks like an individual
    return "INDIVIDUAL" if (client_name or "").strip() else "UNKNOWN"


def classify_signal(entity_type: str, txn_type: str, value_cr: float | None) -> str | None:
    is_inst = entity_type in ("FII", "MF", "INSURANCE")
    t = (txn_type or "").upper()
    is_buy = t.startswith("B")
    is_sell = t.startswith("S")
    v = value_cr or 0
    if is_inst and is_buy:
        return "INSTITUTIONAL_BUY_LARGE" if v >= INST_BUY_LARGE_CR else "INSTITUTIONAL_BUY"
    if is_inst and is_sell and v >= INST_SELL_LARGE_CR:
        return "INSTITUTIONAL_SELL_LARGE"
    if entity_type == "PROMOTER" and is_buy:
        return "PROMOTER_OPEN_MARKET_BUY"
    return None


def run_pass(conn: sqlite3.Connection, date: str | None = None) -> dict:
    """Classify the day's bulk/block deals. `date` defaults to the latest deal_date."""
    if date is None:
        r = conn.execute("SELECT MAX(deal_date) FROM raw_large_deals").fetchone()
        date = r[0] if r else None
    if not date:
        return {"deals": 0, "signals": 0}
    rows = conn.execute(
        "SELECT fingerprint, deal_date, symbol, deal_type, client_name, buy_sell, quantity, "
        "weighted_avg_price FROM raw_large_deals WHERE deal_date=?", (date,)).fetchall()
    signals = 0
    for fp, dd, sym, dtype, client, side, qty, px in rows:
        value_cr = round((qty or 0) * (px or 0) / 1e7, 2) if (qty and px) else None
        entity = classify_entity(client)
        sig = classify_signal(entity, side or "", value_cr)
        txn = "BUY" if (side or "").upper().startswith("B") else \
              "SELL" if (side or "").upper().startswith("S") else (side or "")
        conn.execute(
            "INSERT OR REPLACE INTO large_deal_signals (fingerprint, deal_date, symbol, deal_type, "
            "client_name, txn_type, qty, price, value_cr, entity_type, signal_type, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (fp, dd, sym, dtype, client, txn, qty, px, value_cr, entity, sig))
        if sig:
            signals += 1
    conn.commit()
    report = {"date": date, "deals": len(rows), "signals": signals}
    log.info("large_deal_signals", **report)
    return report


def register_large_deal_signals_job(scheduler, db_path: str) -> str:
    """EOD pass after the bulk/block-deal collector. Trading-day + toggle gated."""
    from apscheduler.triggers.cron import CronTrigger

    from ..events.calendar import _feature_enabled
    from ..scheduler import market_hours
    from ..storage.db import open_db
    job_id = "large_deal_signals"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        if not _feature_enabled("large_deal_signals", True):
            return
        conn = open_db(db_path)
        try:
            run_pass(conn)
        except Exception:
            log.exception("large_deal_signals_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=16, minute=30, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True)
    return job_id
