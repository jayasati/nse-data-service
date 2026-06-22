"""
Universe selectors for indicator jobs.

The EOD compute can afford to sweep the full bhavcopy universe (~2,700
symbols, ~90 seconds). The intraday compute fires every minute during market
hours, so it scopes down to the *tradable* set — F&O + Nifty 500. RSI/MACD
signals on illiquid micro-caps are unreliable and tradeable size is small.

Reads two existing reference tables maintained by the regular collectors:
    raw_fno_list           — F&O-eligible stocks (~209 symbols), updated daily
    raw_index_members      — index → symbol mapping (~12 indices), weekly refresh
"""

from __future__ import annotations

import sqlite3

from ..scheduler import market_hours

# Default size of the core live universe (top F&O by traded value).
CORE_SIZE = 200


def all_equity_symbols(conn: sqlite3.Connection) -> list[str]:
    """Every EQ symbol present in raw_bhavcopy_cm. Sorted, deduplicated."""
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM raw_bhavcopy_cm "
        "WHERE series = 'EQ' ORDER BY symbol"
    ).fetchall()
    return [r[0] for r in rows]


def fno_plus_nifty500(conn: sqlite3.Connection) -> list[str]:
    """Tradable intraday universe: F&O ∪ Nifty 500. Sorted, deduplicated."""
    members = set()
    if _has_table(conn, "raw_fno_list"):
        members.update(r[0] for r in conn.execute("SELECT symbol FROM raw_fno_list"))
    if _has_table(conn, "raw_index_members"):
        members.update(r[0] for r in conn.execute(
            "SELECT symbol FROM raw_index_members WHERE index_name = 'NIFTY 500'"
        ))
    return sorted(members)


def top_fno_by_value(conn: sqlite3.Connection, limit: int = CORE_SIZE) -> list[str]:
    """The `limit` most-liquid F&O names by latest-session traded value.

    Ranks raw_fno_list symbols by `turnover_lacs` on the most recent bhavcopy
    date. Falls back to the full F&O list (or empty) when ranking data is thin —
    so the core never silently collapses to nothing.
    """
    if not _has_table(conn, "raw_fno_list"):
        return []
    fno = [r[0] for r in conn.execute("SELECT symbol FROM raw_fno_list")]
    if not _has_table(conn, "raw_bhavcopy_cm"):
        return sorted(fno)

    ranked = [r[0] for r in conn.execute(
        "SELECT b.symbol FROM raw_bhavcopy_cm b "
        "JOIN raw_fno_list f ON f.symbol = b.symbol "
        "WHERE b.series = 'EQ' AND b.date = (SELECT MAX(date) FROM raw_bhavcopy_cm) "
        "AND b.turnover_lacs IS NOT NULL "
        "ORDER BY b.turnover_lacs DESC LIMIT ?",
        (limit,),
    )]
    if not ranked:
        return sorted(fno)
    # Pad with any unranked F&O names (e.g. no-trade day) up to limit.
    if len(ranked) < limit:
        for s in sorted(fno):
            if s not in ranked:
                ranked.append(s)
            if len(ranked) >= limit:
                break
    return ranked[:limit]


def active_watchlist(conn: sqlite3.Connection, now_iso: str | None = None,
                     limit: int | None = None) -> set[str]:
    """Symbols on the live watchlist whose trigger hasn't expired (most-recent first if limited)."""
    if not _has_table(conn, "live_watchlist"):
        return set()
    now_iso = now_iso or market_hours.now_ist().isoformat()
    sql = "SELECT symbol FROM live_watchlist WHERE expires_at > ? ORDER BY added_at DESC"
    params: tuple = (now_iso,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (now_iso, int(limit))
    return {r[0] for r in conn.execute(sql, params)}


WATCHLIST_CAP = 200


def live_universe(conn: sqlite3.Connection, *, core_size: int = CORE_SIZE,
                  watchlist_cap: int = WATCHLIST_CAP) -> list[str]:
    """What the intraday jobs compute: top-F&O core ∪ a BOUNDED active watchlist.

    The watchlist contribution is capped (most-recently-triggered first) so a runaway
    populator — e.g. the news signal flooding it with ~2k names — can't blow the per-minute
    intraday pass up to the whole market and starve every other writer with DB-lock contention.
    Core (≤core_size) + watchlist (≤watchlist_cap) keeps the live set ≲400 and the 1-minute loop fast.
    """
    members = set(top_fno_by_value(conn, core_size))
    members |= active_watchlist(conn, limit=watchlist_cap)
    return sorted(members)


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None
