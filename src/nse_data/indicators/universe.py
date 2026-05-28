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


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None
