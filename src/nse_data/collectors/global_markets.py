"""Global / cross-asset market context — Phase-1 inputs the NSE feeds don't carry.

Source: yfinance (already a dependency). Pulls US indices, VIX, US 10Y yield, DXY, crude, gold,
Asia — AND fresh Indian indices from Yahoo (^NSEI/^NSEBANK), which sidesteps the stale broker
candle feed. Populates raw_global_markets so the desk report's risk-on/off + gap bias is real.

Run pre-open (~08:15 IST) to capture the US close + Asian session before NSE opens.
"""
from __future__ import annotations

import time

import structlog

log = structlog.get_logger()

# ticker → (display name, asset_class)
TICKERS = {
    "^NSEI": ("Nifty 50", "india_index"), "^NSEBANK": ("Nifty Bank", "india_index"),
    "^CNXIT": ("Nifty IT", "india_index"), "^CNXFIN": ("Nifty Financial", "india_index"),
    # sector indices (broaden the conviction engine's sector-strength check beyond IT/bank/fin)
    "^CNXAUTO": ("Nifty Auto", "india_sector"), "^CNXPHARMA": ("Nifty Pharma", "india_sector"),
    "^CNXFMCG": ("Nifty FMCG", "india_sector"), "^CNXMETAL": ("Nifty Metal", "india_sector"),
    "^CNXENERGY": ("Nifty Energy", "india_sector"), "^CNXREALTY": ("Nifty Realty", "india_sector"),
    "^CNXINFRA": ("Nifty Infra", "india_sector"), "^CNXPSUBANK": ("Nifty PSU Bank", "india_sector"),
    "^CNXMEDIA": ("Nifty Media", "india_sector"),
    "^GSPC": ("S&P 500", "us_index"), "^IXIC": ("Nasdaq", "us_index"),
    "^DJI": ("Dow Jones", "us_index"), "^VIX": ("US VIX", "vol"),
    "^TNX": ("US 10Y Yield", "rate"), "DX-Y.NYB": ("Dollar Index (DXY)", "fx"),
    "CL=F": ("WTI Crude", "commodity"), "BZ=F": ("Brent Crude", "commodity"),
    "GC=F": ("Gold", "commodity"), "^N225": ("Nikkei 225", "asia"),
    "^HSI": ("Hang Seng", "asia"), "000001.SS": ("Shanghai Composite", "asia"),
}


def fetch_global() -> list[dict]:
    """Last close + prior close per ticker (yfinance). Per-ticker failures are skipped."""
    import yfinance as yf

    as_of, rows = int(time.time()), []
    for tk, (name, cls) in TICKERS.items():
        try:
            h = yf.Ticker(tk).history(period="5d")
            if h is None or len(h) < 2:
                continue
            last, prev = float(h["Close"].iloc[-1]), float(h["Close"].iloc[-2])
            if not prev:
                continue
            rows.append({"ticker": tk, "name": name, "asset_class": cls, "as_of": as_of,
                         "last": round(last, 2), "prev_close": round(prev, 2),
                         "change": round(last - prev, 2),
                         "pct_change": round((last - prev) / prev * 100, 2), "captured_at": as_of})
        except Exception:  # noqa: BLE001 — one bad ticker shouldn't sink the snapshot
            continue
    return rows


def run_global_markets(db_path: str) -> dict:
    from ..storage.db import open_db
    rows = fetch_global()
    conn = open_db(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO raw_global_markets (ticker, name, asset_class, as_of, last, "
            "prev_close, change, pct_change, captured_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [(r["ticker"], r["name"], r["asset_class"], r["as_of"], r["last"], r["prev_close"],
              r["change"], r["pct_change"], r["captured_at"]) for r in rows])
        conn.commit()
    finally:
        conn.close()
    return {"fetched": len(rows), "tickers": [r["ticker"] for r in rows]}


def register_global_markets_job(scheduler, db_path: str) -> str:
    """Pre-open snapshot at 08:15 IST (US close + Asia in hand) + a 17:00 evening refresh."""
    from apscheduler.triggers.cron import CronTrigger

    from ..scheduler import market_hours

    def _tick():
        try:
            log.info("global_markets", **run_global_markets(db_path))
        except Exception:
            log.exception("global_markets_failed")

    scheduler.add_job(_tick, trigger=CronTrigger(hour="8,17", minute=15,
                      timezone=market_hours.IST), id="global_markets", max_instances=1,
                      coalesce=True, replace_existing=True)
    return "global_markets"
