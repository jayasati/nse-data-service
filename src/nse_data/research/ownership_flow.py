"""Per-stock ownership flows for the pre-buy card (PROFITABILITY_PLAN Track C, R11).

Who is accumulating / distributing a name, from the disclosure data we have:
  block/bulk deals  raw_large_deals — net institutional buy − sell over a window (value + count)
  insider/promoter  raw_insider_trading — net promoter/insider buying (degrades to None when
                    the feed is empty, which it is until the collector populates it)

Per-stock FII/DII flow is NOT here — NSE publishes FII/DII only at the market level, not
per scrip (an open item in the plan).

NOTE on dates: raw_large_deals.deal_date is NSE format 'DD-Mon-YYYY' (e.g. '26-May-2026'),
which sorts AFTER ISO strings lexically — so a naive `deal_date >= date(...)` matches
everything. We parse it properly and measure recency against the latest deal in the table
(robust on a stale DB snapshot). 'short' deals (short-sale disclosures, null buy/sell) are
excluded from the buy/sell flow.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta


def _has(conn, name) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _nse_date(s: str | None):
    try:
        return datetime.strptime(s, "%d-%b-%Y").date()
    except (ValueError, TypeError):
        return None


def _ref_date(conn):
    """Latest deal date in the table — the recency anchor (snapshot-safe)."""
    ref = None
    for (d,) in conn.execute("SELECT DISTINCT deal_date FROM raw_large_deals"):
        pd = _nse_date(d)
        if pd and (ref is None or pd > ref):
            ref = pd
    return ref


def block_flow(conn: sqlite3.Connection, symbol: str, *, days: int = 90) -> dict | None:
    """Net block/bulk-deal flow for a symbol over the last `days`. None if no table/deals."""
    if not _has(conn, "raw_large_deals"):
        return None
    ref = _ref_date(conn)
    if ref is None:
        return None
    cutoff = ref - timedelta(days=days)
    rows = conn.execute(
        "SELECT deal_date, buy_sell, quantity, weighted_avg_price FROM raw_large_deals "
        "WHERE symbol=? AND deal_type IN ('block','bulk') AND buy_sell IN ('BUY','SELL')",
        (symbol,)).fetchall()
    buy_v = sell_v = 0.0
    buy_n = sell_n = 0
    for dd, bs, qty, watp in rows:
        d = _nse_date(dd)
        if d is None or d < cutoff:
            continue
        val = (qty * watp / 1e7) if (qty and watp) else 0.0      # ₹ crore
        if bs == "BUY":
            buy_n += 1
            buy_v += val
        else:
            sell_n += 1
            sell_v += val
    if buy_n + sell_n == 0:
        return None
    return {"days": days, "buy_deals": buy_n, "sell_deals": sell_n,
            "buy_value_cr": round(buy_v, 1), "sell_value_cr": round(sell_v, 1),
            "net_value_cr": round(buy_v - sell_v, 1)}


def insider_flow(conn: sqlite3.Connection, symbol: str, *, days: int = 180) -> dict | None:
    """Net promoter/insider buying for a symbol. None when the feed is empty (current state)."""
    if not _has(conn, "raw_insider_trading"):
        return None
    rows = conn.execute(
        "SELECT acquirer_category, transaction_type, value_in_rupees, intimation_date "
        "FROM raw_insider_trading WHERE symbol=?", (symbol,)).fetchall()
    if not rows:
        return None
    net_v = 0.0
    promoter_buy = False
    n = 0
    for cat, txn, val, _dt in rows:
        if val is None or txn is None:
            continue
        n += 1
        signed = (val if "buy" in txn.lower() or "acqui" in txn.lower() else -val) / 1e7
        net_v += signed
        if signed > 0 and cat and "promoter" in cat.lower():
            promoter_buy = True
    if n == 0:
        return None
    return {"n": n, "net_value_cr": round(net_v, 1), "promoter_buying": promoter_buy}


def ownership_flow(conn: sqlite3.Connection, symbol: str) -> dict:
    """Combined block + insider flow for the card. Sections are None when their data is absent."""
    return {"block": block_flow(conn, symbol), "insider": insider_flow(conn, symbol)}
